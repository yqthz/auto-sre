"""
AI Chat 消息服务层
处理消息发送、Agent 执行、流式响应等核心逻辑
"""
import json
from datetime import datetime
from typing import AsyncGenerator, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from app.model.chat import ChatSession, ChatMessage
from app.model.audit_log import AuditLog
from app.agent.graph import create_graph, SENSITIVE_TOOLS
from app.agent.tools.security import before_tool_execution, after_tool_execution, TOOL_REGISTRY
from app.core.logger import logger


class ChatService:
    """AI Chat 服务"""

    def __init__(self):
        self.graph = create_graph()

    async def save_message(
        self,
        db: AsyncSession,
        session_id: int,
        role: str,
        content: Optional[str] = None,
        tool_calls: Optional[list] = None,
        tool_call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        requires_approval: bool = False,
        status: str = "completed"
    ) -> ChatMessage:
        """保存消息到数据库"""
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            requires_approval=requires_approval,
            approval_status="pending" if requires_approval else None,
            status=status
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)

        # 更新会话的最后消息时间
        session_query = select(ChatSession).where(ChatSession.id == session_id)
        session_result = await db.execute(session_query)
        session = session_result.scalars().first()
        if session:
            session.last_message_at = datetime.utcnow()
            await db.commit()

        return message

    async def get_session_messages(
        self,
        db: AsyncSession,
        session_id: int,
        user_id: int
    ) -> list[ChatMessage]:
        """获取会话的所有消息"""
        # 验证会话所有权
        session_query = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id
        )
        session_result = await db.execute(session_query)
        session = session_result.scalars().first()
        if not session:
            raise ValueError("Session not found or access denied")

        # 获取消息
        query = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
        result = await db.execute(query)
        return result.scalars().all()

    def format_tool_call_display(self, tool_call: dict) -> str:
        """格式化工具调用信息，用于前端展示"""
        tool_name = tool_call.get("name", "unknown")
        args = tool_call.get("args", {})

        # 获取工具元数据
        tool_meta = TOOL_REGISTRY.get(tool_name, {})
        permission = tool_meta.get("permission", "unknown")

        # 构建展示文本
        display = f"### 🔧 工具调用: {tool_name}\n\n"
        display += f"**权限级别**: {permission}\n\n"
        display += "**参数**:\n```json\n"
        display += json.dumps(args, indent=2, ensure_ascii=False)
        display += "\n```\n\n"

        # 如果是敏感工具，显示警告
        if tool_name in SENSITIVE_TOOLS:
            display += "⚠️ **这是一个危险操作，需要您的授权！**\n\n"

        return display

    def format_shell_command_display(self, tool_name: str, args: dict) -> Optional[str]:
        """提取并格式化 Shell 命令，用于前端展示"""
        # 根据工具类型提取实际执行的命令
        command = None

        if tool_name == "exec_command_in_container":
            command = args.get("command", "")
        elif tool_name == "restart_server":
            container = args.get("container_name", "")
            command = f"docker restart {container}"
        elif tool_name == "fetch_remote_log":
            file_path = args.get("file_path", "")
            lines = args.get("lines", 50)
            command = f"tail -n {lines} {file_path}"
        elif tool_name == "grep_remote_log":
            file_path = args.get("file_path", "")
            pattern = args.get("pattern", "")
            command = f"grep '{pattern}' {file_path}"

        if command:
            return f"```bash\n{command}\n```"
        return None

    async def continue_agent_execution(
        self,
        db: AsyncSession,
        session: ChatSession,
        user_id: int,
        user_role: str,
        approved: bool,
        rejection_reason: Optional[str] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        继续执行被暂停的 Agent（授权后）

        参数:
        - approved: True 批准执行，False 拒绝执行
        - rejection_reason: 拒绝原因
        """
        try:
            config = {
                "configurable": {
                    "thread_id": session.thread_id,
                    "user_id": str(user_id),
                    "user_role": user_role
                }
            }

            if not approved:
                # 拒绝执行：注入拒绝消息
                from langchain_core.messages import HumanMessage

                reject_msg = f"用户拒绝执行该操作。原因: {rejection_reason or '未提供原因'}"

                # 更新状态，添加拒绝消息
                self.graph.update_state(
                    config,
                    {"messages": [HumanMessage(content=reject_msg)]},
                    as_node="tools"  # 作为 tools 节点的输出
                )

                yield {
                    "event": "tool_rejected",
                    "data": {"message": reject_msg}
                }

                # 继续执行 Agent，让它处理拒绝
                async for event in self.graph.astream(None, config=config, stream_mode="values"):
                    if "messages" in event:
                        last_message = event["messages"][-1]

                        if isinstance(last_message, AIMessage):
                            if last_message.content:
                                yield {
                                    "event": "agent_message_delta",
                                    "data": {"delta": last_message.content}
                                }

                            # 保存 AI 消息
                            await self.save_message(
                                db,
                                session.id,
                                "assistant",
                                content=last_message.content
                            )

                yield {
                    "event": "agent_message_complete",
                    "data": {"message": "Agent 已处理拒绝"}
                }

            else:
                # 批准执行：继续执行工具
                yield {
                    "event": "tool_approved",
                    "data": {"message": "工具调用已批准，正在执行..."}
                }

                # 继续执行（不需要更新状态，直接 stream）
                async for event in self.graph.astream(None, config=config, stream_mode="values"):
                    if "messages" in event:
                        last_message = event["messages"][-1]

                        # 工具执行结果
                        if isinstance(last_message, ToolMessage):
                            yield {
                                "event": "tool_call_result",
                                "data": {
                                    "tool_call_id": last_message.tool_call_id,
                                    "tool_name": last_message.name,
                                    "result": last_message.content[:1000]
                                }
                            }

                            # 保存工具结果
                            await self.save_message(
                                db,
                                session.id,
                                "tool",
                                content=last_message.content,
                                tool_call_id=last_message.tool_call_id,
                                tool_name=last_message.name
                            )

                        # AI 后续回复
                        elif isinstance(last_message, AIMessage):
                            if last_message.content:
                                yield {
                                    "event": "agent_message_delta",
                                    "data": {"delta": last_message.content}
                                }

                            # 检查是否又有新的工具调用
                            if last_message.tool_calls:
                                for tool_call in last_message.tool_calls:
                                    tool_name = tool_call["name"]
                                    is_sensitive = tool_name in SENSITIVE_TOOLS

                                    if is_sensitive:
                                        # 又遇到敏感工具，再次暂停
                                        tool_display = self.format_tool_call_display(tool_call)
                                        shell_command = self.format_shell_command_display(
                                            tool_name, tool_call["args"]
                                        )

                                        yield {
                                            "event": "tool_call_start",
                                            "data": {
                                                "tool_call_id": tool_call["id"],
                                                "tool_name": tool_name,
                                                "args": tool_call["args"],
                                                "display": tool_display,
                                                "shell_command": shell_command,
                                                "is_sensitive": True,
                                                "requires_approval": True
                                            }
                                        }

                                        await self.save_message(
                                            db,
                                            session.id,
                                            "assistant",
                                            content=last_message.content,
                                            tool_calls=[tool_call],
                                            requires_approval=True,
                                            status="pending"
                                        )

                                        session.status = "waiting"
                                        await db.commit()

                                        yield {
                                            "event": "tool_approval_required",
                                            "data": {
                                                "tool_call_id": tool_call["id"],
                                                "tool_name": tool_name,
                                                "message": f"需要您的授权才能执行 {tool_name}"
                                            }
                                        }
                                        return

                            # 保存最终消息
                            if last_message.content:
                                await self.save_message(
                                    db,
                                    session.id,
                                    "assistant",
                                    content=last_message.content
                                )

                yield {
                    "event": "agent_message_complete",
                    "data": {"message": "执行完成"}
                }

        except Exception as e:
            logger.error(f"Error in continue_agent_execution: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {"message": str(e)}
            }

    async def stream_agent_response(
        self,
        db: AsyncSession,
        session: ChatSession,
        user_message: str,
        user_id: int,
        user_role: str
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        流式执行 Agent 并返回事件

        事件类型:
        - user_message: 用户消息已保存
        - agent_thinking: Agent 正在思考
        - agent_message_delta: Agent 消息增量
        - tool_call_start: 工具调用开始
        - tool_call_result: 工具调用结果
        - tool_approval_required: 需要用户授权
        - agent_message_complete: Agent 消息完成
        - error: 错误
        """
        try:
            # 1. 保存用户消息
            user_msg = await self.save_message(
                db, session.id, "user", content=user_message
            )
            yield {
                "event": "user_message",
                "data": {
                    "message_id": user_msg.id,
                    "content": user_message
                }
            }

            # 2. 构建 LangGraph 配置
            config = {
                "configurable": {
                    "thread_id": session.thread_id,
                    "user_id": str(user_id),
                    "user_role": user_role
                }
            }

            # 3. 准备初始输入
            initial_input = {
                "messages": [HumanMessage(content=user_message)],
                "user_role": user_role,
                "mode": session.mode
            }

            # 4. 流式执行 Agent
            yield {"event": "agent_thinking", "data": {}}

            assistant_message_content = ""
            current_tool_calls = []

            async for event in self.graph.astream(initial_input, config=config, stream_mode="values"):
                if "messages" in event:
                    last_message = event["messages"][-1]

                    # AI 消息
                    if isinstance(last_message, AIMessage):
                        # 流式输出内容
                        if last_message.content:
                            delta = last_message.content[len(assistant_message_content):]
                            if delta:
                                assistant_message_content = last_message.content
                                yield {
                                    "event": "agent_message_delta",
                                    "data": {"delta": delta}
                                }

                        # 工具调用
                        if last_message.tool_calls:
                            current_tool_calls = last_message.tool_calls

                            for tool_call in last_message.tool_calls:
                                tool_name = tool_call["name"]
                                args = tool_call["args"]

                                # 检查是否是敏感工具
                                is_sensitive = tool_name in SENSITIVE_TOOLS

                                # 格式化工具调用展示
                                tool_display = self.format_tool_call_display(tool_call)
                                shell_command = self.format_shell_command_display(tool_name, args)

                                yield {
                                    "event": "tool_call_start",
                                    "data": {
                                        "tool_call_id": tool_call["id"],
                                        "tool_name": tool_name,
                                        "args": args,
                                        "display": tool_display,
                                        "shell_command": shell_command,
                                        "is_sensitive": is_sensitive,
                                        "requires_approval": is_sensitive
                                    }
                                }

                                # 如果是敏感工具，暂停并等待授权
                                if is_sensitive:
                                    # 保存 AI 消息（包含工具调用）
                                    await self.save_message(
                                        db,
                                        session.id,
                                        "assistant",
                                        content=assistant_message_content,
                                        tool_calls=[tool_call],
                                        requires_approval=True,
                                        status="pending"
                                    )

                                    # 更新会话状态为等待授权
                                    session.status = "waiting"
                                    await db.commit()

                                    yield {
                                        "event": "tool_approval_required",
                                        "data": {
                                            "tool_call_id": tool_call["id"],
                                            "tool_name": tool_name,
                                            "message": f"需要您的授权才能执行 {tool_name}"
                                        }
                                    }

                                    # 暂停执行，等待用户授权
                                    return

                    # 工具执行结果
                    elif isinstance(last_message, ToolMessage):
                        yield {
                            "event": "tool_call_result",
                            "data": {
                                "tool_call_id": last_message.tool_call_id,
                                "tool_name": last_message.name,
                                "result": last_message.content[:1000]  # 限制长度
                            }
                        }

                        # 保存工具结果消息
                        await self.save_message(
                            db,
                            session.id,
                            "tool",
                            content=last_message.content,
                            tool_call_id=last_message.tool_call_id,
                            tool_name=last_message.name
                        )

            # 5. 保存最终的 AI 消息
            if assistant_message_content or current_tool_calls:
                await self.save_message(
                    db,
                    session.id,
                    "assistant",
                    content=assistant_message_content,
                    tool_calls=current_tool_calls if current_tool_calls else None
                )

            yield {
                "event": "agent_message_complete",
                "data": {"content": assistant_message_content}
            }

        except Exception as e:
            logger.error(f"Error in stream_agent_response: {e}", exc_info=True)
            yield {
                "event": "error",
                "data": {"message": str(e)}
            }


# 全局单例
chat_service = ChatService()

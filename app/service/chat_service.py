"""
AI Chat 消息服务层
处理消息发送、Agent 执行、流式响应等核心逻辑
"""
import json
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from app.model.chat import ChatSession, ChatMessage
from app.agent.graph import create_graph
from app.agent.approval_policy import tool_approval_profile
from app.agent.trace import ToolTrace
from app.agent.tools.audit import audit_tool_event
from app.agent.trace_runtime import trace_runtime
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

        # 统一审批策略来源（含 dispatch action 映射）
        approval_profile = tool_approval_profile(tool_name, args)
        permission = str(approval_profile.get("permission") or "unknown")

        # 构建展示文本
        display = f"### 🔧 工具调用: {tool_name}\n\n"
        display += f"**权限级别**: {permission}\n\n"
        display += "**参数**:\n```json\n"
        display += json.dumps(args, indent=2, ensure_ascii=False)
        display += "\n```\n\n"

        # 如果是敏感工具，显示警告
        if bool(tool_approval_profile(tool_name, args).get("requires_approval", False)):
            display += "⚠️ **这是一个危险操作，需要您的授权！**\n\n"

        return display

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _build_approval_request(self, tool_call: dict) -> dict:
        tool_name = tool_call.get("name", "unknown")
        args = tool_call.get("args", {})

        approval_profile = tool_approval_profile(tool_name, args)
        permission = str(approval_profile.get("permission") or "unknown")
        risk_level = str(approval_profile.get("risk_level") or "low")
        impact = f"execute tool `{tool_name}` with provided args in manual mode"

        if tool_name == "dispatch_tool":
            action = str(approval_profile.get("action") or args.get("action") or "")
            if action:
                impact = f"execute action `{action}` through dispatch_tool"

        return {
            "approval_id": tool_call.get("id"),
            "tool_call_id": tool_call.get("id"),
            "tool_name": tool_name,
            "args": args,
            "permission": permission,
            "risk_level": risk_level,
            "impact": impact,
            "rollback": "not_provided",
            "status": "pending",
            "created_at": self._utc_now_iso(),
        }

    @staticmethod
    def _append_state_item(snapshot_values: dict, field: str, item: dict) -> List[dict]:
        current = snapshot_values.get(field, [])
        if not isinstance(current, list):
            current = []
        return [*current, item]

    def _find_pending_sensitive_tool_call(self, snapshot_values: dict) -> Optional[dict]:
        """查找等待审批的工具调用"""
        last_message = (snapshot_values.get("messages") or [None])[-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            for tc in last_message.tool_calls:
                if bool(tool_approval_profile(tc.get("name", ""), tc.get("args", {})).get("requires_approval", False)):
                    return tc
        return None

    
    def _trace_run_id_from_state(snapshot_values: dict) -> Optional[str]:
        run_id = snapshot_values.get("trace_run_id")
        return str(run_id) if run_id else None


    @staticmethod
    def _safe_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _dispatch_request_extra(tool_call: dict) -> Dict[str, Any]:
        if str(tool_call.get("name") or "") != "dispatch_tool":
            return {}
        args = tool_call.get("args", {})
        if not isinstance(args, dict):
            return {}
        return {"action": args.get("action")}

    @staticmethod
    def _dispatch_result_extra(tool_message: ToolMessage) -> Dict[str, Any]:
        if str(tool_message.name or "") != "dispatch_tool":
            return {}
        content = tool_message.content
        if not isinstance(content, str):
            return {}
        try:
            payload = json.loads(content)
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            "action": payload.get("action"),
            "dispatch_status": payload.get("status"),
            "execution_backend": payload.get("execution_backend"),
            "attempts": payload.get("attempts"),
            "last_error_kind": payload.get("last_error_kind"),
            "risk_level": payload.get("risk_level"),
            "requires_approval": payload.get("requires_approval"),
        }

    def _trace_tool_start(self, run_id: Optional[str], tool_call: dict) -> None:
        """追踪工具调用"""
        ToolTrace.on_tool_start(run_id=run_id, tool_call=tool_call)

    def _audit_tool_request(
        self,
        *,
        tool_call: dict,
        user_id: int,
        user_role: str,
        session: ChatSession,
        run_id: Optional[str],
        status: str = "requested",
    ) -> None:
        audit_tool_event(
            "tool_call_request",
            tool=str(tool_call.get("name") or "unknown"),
            user_id=str(user_id),
            user_role=user_role,
            mode=session.mode,
            thread_id=session.thread_id,
            trace_run_id=run_id,
            tool_call_id=tool_call.get("id"),
            args=tool_call.get("args", {}),
            status=status,
            extra={"session_id": session.id, **self._dispatch_request_extra(tool_call)},
        )

    def _audit_tool_result(
        self,
        *,
        tool_message: ToolMessage,
        user_id: int,
        user_role: str,
        session: ChatSession,
        run_id: Optional[str],
    ) -> None:
        audit_tool_event(
            "tool_call_result",
            tool=str(tool_message.name or "unknown"),
            user_id=str(user_id),
            user_role=user_role,
            mode=session.mode,
            thread_id=session.thread_id,
            trace_run_id=run_id,
            tool_call_id=tool_message.tool_call_id,
            result=str(tool_message.content),
            status="success",
            extra={"session_id": session.id, **self._dispatch_result_extra(tool_message)},
        )

    def _trace_tool_end(self, run_id: Optional[str], tool_message: ToolMessage) -> None:
        """最终工具调用结果"""
        ToolTrace.on_tool_end(
            run_id=run_id,
            call_id=tool_message.tool_call_id,
            tool_name=str(tool_message.name or "unknown"),
            output_preview=self._safe_text(tool_message.content),
            status="success",
        )

    def record_approval_outcome(
        self,
        session: ChatSession,
        status: str,
        approved: bool,
        actor_id: int,
        actor_role: str,
        reason: Optional[str] = None,
    ) -> Optional[dict]:
        """审批结果写回 state"""
        config = {"configurable": {"thread_id": session.thread_id, "mode": session.mode}}
        # 找到会话图状态
        snapshot = self.graph.get_state(config)
        snapshot_values = snapshot.values or {}
        # 找到待审批的工具调用
        pending_tool_call = self._find_pending_sensitive_tool_call(snapshot_values)
        if not pending_tool_call:
            return None

        tool_call_id = pending_tool_call.get("id")
        # 从状态中获取 approval_requests
        approval_requests = snapshot_values.get("approval_requests", [])
        if not isinstance(approval_requests, list):
            approval_requests = []

        updated_requests = []
        # 遍历每条审批记录
        for item in approval_requests:
            if not isinstance(item, dict):
                updated_requests.append(item)
                continue
            if item.get("tool_call_id") != tool_call_id:
                updated_requests.append(item)
                continue
            next_item = dict(item)
            next_item["status"] = status
            next_item["updated_at"] = self._utc_now_iso()
            if reason:
                next_item["reason"] = reason
            updated_requests.append(next_item)

        # 构造 action_record
        action_record = {
            "tool_call_id": tool_call_id,
            "tool_name": pending_tool_call.get("name"),
            "args": pending_tool_call.get("args", {}),
            "status": status,
            "approved": approved,
            "executed": False,
            "reason": reason,
            "actor_id": str(actor_id),
            "actor_role": actor_role,
            "timestamp": self._utc_now_iso(),
        }

        # 更新 state
        self.graph.update_state(
            config,
            {
                "approval_requests": updated_requests,
                "actions_executed": self._append_state_item(
                    snapshot_values, "actions_executed", action_record
                ),
            },
            as_node="tools",
        )
        return action_record

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
            # 创建 trace run
            run_id = trace_runtime.start_run(
                session_id=session.id,
                user_id=user_id,
                mode=session.mode,
                trigger_type="approval_continue",
            )
            config = {
                "configurable": {
                    "thread_id": session.thread_id,
                    "user_id": str(user_id),
                    "user_role": user_role,
                    "mode": session.mode,
                    "trace_run_id": run_id,
                }
            }

            yield {
                "event": "trace_run_start",
                "data": {"run_id": run_id}
            }
            # 获取 state
            snapshot = self.graph.get_state(config)
            snapshot_values = snapshot.values or {}

            # 找到最后一个待审批的 tool call
            last_message = (snapshot_values.get("messages") or [None])[-1]

            pending_tool_call = None
            if isinstance(last_message, AIMessage) and last_message.tool_calls:
                for tc in last_message.tool_calls:
                    if bool(tool_approval_profile(tc.get("name", ""), tc.get("args", {})).get("requires_approval", False)):
                        pending_tool_call = tc
                        break

            pending_tool_call_id = pending_tool_call.get("id") if pending_tool_call else None
            approval_requests = snapshot_values.get("approval_requests", [])
            if not isinstance(approval_requests, list):
                approval_requests = []

            # 修改审批状态
            def mark_approval_status(status: str, reason: Optional[str] = None) -> List[dict]:
                updated = []
                for item in approval_requests:
                    if not isinstance(item, dict):
                        updated.append(item)
                        continue
                    if item.get("tool_call_id") != pending_tool_call_id:
                        updated.append(item)
                        continue
                    next_item = dict(item)
                    next_item["status"] = status
                    next_item["updated_at"] = self._utc_now_iso()
                    if reason:
                        next_item["reason"] = reason
                    updated.append(next_item)
                return updated

            if not approved:
                # 拒绝执行：注入拒绝消息
                from langchain_core.messages import HumanMessage

                # 构造拒绝信息
                reject_msg = f"用户拒绝执行该操作。原因: {rejection_reason or '未提供原因'}"
                reject_reason_text = rejection_reason or "rejected by user"
                rejected_tool_messages: List[ToolMessage] = []
                if isinstance(last_message, AIMessage) and last_message.tool_calls:
                    for tc in last_message.tool_calls:
                        tc_id = tc.get("id")
                        if not tc_id:
                            continue
                        rejected_tool_messages.append(
                            ToolMessage(
                                content=f"tool execution skipped: {reject_reason_text}",
                                tool_call_id=tc_id,
                                name=tc.get("name"),
                            )
                        )

                # 更新状态，添加拒绝消息
                self.graph.update_state(
                    config,
                    {"messages": [*rejected_tool_messages, HumanMessage(content=reject_msg)]},
                    as_node="tools"  # 作为 tools 节点的输出
                )

                if pending_tool_call:
                    action_record = {
                        "tool_call_id": pending_tool_call.get("id"),
                        "tool_name": pending_tool_call.get("name"),
                        "args": pending_tool_call.get("args", {}),
                        "status": "rejected",
                        "approved": False,
                        "reason": rejection_reason,
                        "executed": False,
                        "timestamp": self._utc_now_iso(),
                    }
                    self.graph.update_state(
                        config,
                        {
                            "approval_requests": mark_approval_status(
                                "rejected", reason=rejection_reason
                            ),
                            "actions_executed": self._append_state_item(
                                snapshot_values, "actions_executed", action_record
                            )
                        },
                        as_node="tools",
                    )

                # 发送 tool rejected
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
                if pending_tool_call:
                    action_record = {
                        "tool_call_id": pending_tool_call.get("id"),
                        "tool_name": pending_tool_call.get("name"),
                        "args": pending_tool_call.get("args", {}),
                        "status": "approved",
                        "approved": True,
                        "executed": False,
                        "timestamp": self._utc_now_iso(),
                    }
                    self.graph.update_state(
                        config,
                        {
                            "approval_requests": mark_approval_status("approved"),
                            "actions_executed": self._append_state_item(
                                snapshot_values, "actions_executed", action_record
                            )
                        },
                        as_node="tools",
                    )

                # 发送 tool approved
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
                            self._trace_tool_end(run_id, last_message)
                            self._audit_tool_result(
                                tool_message=last_message,
                                user_id=user_id,
                                user_role=user_role,
                                session=session,
                                run_id=run_id,
                            )
                            tool_action = {
                                "tool_call_id": last_message.tool_call_id,
                                "tool_name": last_message.name,
                                "status": "executed",
                                "approved": True,
                                "executed": True,
                                "result_preview": str(last_message.content)[:500],
                                "timestamp": self._utc_now_iso(),
                            }
                            current_values = self.graph.get_state(config).values or {}
                            self.graph.update_state(
                                config,
                                {
                                    "actions_executed": self._append_state_item(
                                        current_values, "actions_executed", tool_action
                                    )
                                },
                                as_node="tools",
                            )

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
                                    args = tool_call["args"]
                                    requires_approval = bool(tool_approval_profile(tool_name, args).get("requires_approval", False))

                                    if requires_approval:
                                        # 又遇到敏感工具，再次暂停
                                        approval_request = self._build_approval_request(tool_call)
                                        current_values = self.graph.get_state(config).values or {}
                                        self.graph.update_state(
                                            config,
                                            {
                                                "approval_requests": self._append_state_item(
                                                    current_values, "approval_requests", approval_request
                                                )
                                            },
                                            as_node="sre_agent",
                                        )
                                        tool_display = self.format_tool_call_display(tool_call)

                                        self._trace_tool_start(run_id, tool_call)
                                        self._audit_tool_request(
                                            tool_call=tool_call,
                                            user_id=user_id,
                                            user_role=user_role,
                                            session=session,
                                            run_id=run_id,
                                            status="approval_required",
                                        )

                                        yield {
                                            "event": "tool_call_start",
                                            "data": {
                                                "tool_call_id": tool_call["id"],
                                                "tool_name": tool_name,
                                                "args": tool_call["args"],
                                                "display": tool_display,
                                                "requires_approval": True,
                                                "approval_request": approval_request,
                                            }
                                        }

                                        await self.save_message(
                                            db,
                                            session.id,
                                            "assistant",
                                            content=last_message.content,
                                            tool_calls=[{**tool_call, "approval_request": approval_request}],
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
                                                "message": f"需要您的授权才能执行 {tool_name}",
                                                "approval_request": approval_request,
                                            }
                                        }
                                        trace_runtime.end_run(run_id, status="success")
                                        return

                            # 保存最终消息
                            if last_message.content:
                                await self.save_message(
                                    db,
                                    session.id,
                                    "assistant",
                                    content=last_message.content
                                )

                trace_runtime.end_run(run_id, status="success")
                yield {
                    "event": "agent_message_complete",
                    "data": {"message": "执行完成"}
                }

        except Exception as e:
            logger.error(f"Error in continue_agent_execution: {e}", exc_info=True)
            if "run_id" in locals():
                trace_runtime.end_run(run_id, status="failed", error_summary=str(e))
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
            run_id = trace_runtime.start_run(
                session_id=session.id,
                user_id=user_id,
                mode=session.mode,
                trigger_type="message",
                trigger_message_id=user_msg.id,
            )
            config = {
                "configurable": {
                    "thread_id": session.thread_id,
                    "user_id": str(user_id),
                    "user_role": user_role,
                    "mode": session.mode,
                    "trace_run_id": run_id,
                }
            }

            yield {
                "event": "trace_run_start",
                "data": {"run_id": run_id}
            }

            # 3. 准备初始输入
            initial_input = {
                "messages": [HumanMessage(content=user_message)],
                "user_role": user_role,
                "mode": session.mode,
                # "evidence": [],
                # "hypotheses": [],
                "approval_requests": [],
                "actions_executed": [],
                "trace_run_id": run_id,
            }

            # 4. 流式执行 Agent
            yield {"event": "agent_thinking", "data": {}}

            assistant_message_content = ""
            current_tool_calls = []
            should_auto_resume_tools = False
            traced_tool_call_ids = set()

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
                            only_non_sensitive = True
                            processed_tool_call = False

                            for tool_call in last_message.tool_calls:
                                tool_call_id = tool_call.get("id")
                                if tool_call_id in traced_tool_call_ids:
                                    continue
                                if tool_call_id:
                                    traced_tool_call_ids.add(tool_call_id)
                                processed_tool_call = True

                                tool_name = tool_call["name"]
                                args = tool_call["args"]

                                # 检查是否是敏感工具
                                requires_approval = bool(tool_approval_profile(tool_name, args).get("requires_approval", False))

                                # 格式化工具调用展示
                                tool_display = self.format_tool_call_display(tool_call)

                                self._trace_tool_start(run_id, tool_call)
                                self._audit_tool_request(
                                    tool_call=tool_call,
                                    user_id=user_id,
                                    user_role=user_role,
                                    session=session,
                                    run_id=run_id,
                                    status="approval_required" if requires_approval else "requested",
                                )

                                yield {
                                    "event": "tool_call_start",
                                    "data": {
                                        "tool_call_id": tool_call["id"],
                                        "tool_name": tool_name,
                                        "args": args,
                                        "display": tool_display,
                                        "requires_approval": requires_approval,
                                    }
                                }

                                # 如果是敏感工具，暂停并等待授权
                                if requires_approval:
                                    only_non_sensitive = False
                                    approval_request = self._build_approval_request(tool_call)
                                    current_values = self.graph.get_state(config).values or {}
                                    self.graph.update_state(
                                        config,
                                        {
                                            "approval_requests": self._append_state_item(
                                                current_values, "approval_requests", approval_request
                                            )
                                        },
                                        as_node="sre_agent",
                                    )
                                    # 保存 AI 消息（包含工具调用）
                                    await self.save_message(
                                        db,
                                        session.id,
                                        "assistant",
                                        content=assistant_message_content,
                                        tool_calls=[{**tool_call, "approval_request": approval_request}],
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
                                            "message": f"需要您的授权才能执行 {tool_name}",
                                            "approval_request": approval_request,
                                        }
                                    }

                                    # 暂停执行，等待用户授权
                                    trace_runtime.end_run(run_id, status="success")
                                    return
                            if processed_tool_call and only_non_sensitive:
                                should_auto_resume_tools = True

                    # 工具执行结果
                    elif isinstance(last_message, ToolMessage):
                        self._trace_tool_end(run_id, last_message)
                        self._audit_tool_result(
                            tool_message=last_message,
                            user_id=user_id,
                            user_role=user_role,
                            session=session,
                            run_id=run_id,
                        )
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

            while should_auto_resume_tools:
                should_auto_resume_tools = False
                async for event in self.graph.astream(None, config=config, stream_mode="values"):
                    if "messages" not in event:
                        continue
                    last_message = event["messages"][-1]

                    if isinstance(last_message, ToolMessage):
                        self._trace_tool_end(run_id, last_message)
                        self._audit_tool_result(
                            tool_message=last_message,
                            user_id=user_id,
                            user_role=user_role,
                            session=session,
                            run_id=run_id,
                        )
                        yield {
                            "event": "tool_call_result",
                            "data": {
                                "tool_call_id": last_message.tool_call_id,
                                "tool_name": last_message.name,
                                "result": last_message.content[:1000]
                            }
                        }
                        await self.save_message(
                            db,
                            session.id,
                            "tool",
                            content=last_message.content,
                            tool_call_id=last_message.tool_call_id,
                            tool_name=last_message.name
                        )
                    elif isinstance(last_message, AIMessage):
                        if last_message.content:
                            delta = last_message.content[len(assistant_message_content):]
                            if delta:
                                assistant_message_content = last_message.content
                                yield {
                                    "event": "agent_message_delta",
                                    "data": {"delta": delta}
                                }
                        if last_message.tool_calls:
                            current_tool_calls = last_message.tool_calls
                            only_non_sensitive = True
                            processed_tool_call = False

                            for tool_call in last_message.tool_calls:
                                tool_call_id = tool_call.get("id")
                                if tool_call_id in traced_tool_call_ids:
                                    continue
                                if tool_call_id:
                                    traced_tool_call_ids.add(tool_call_id)
                                processed_tool_call = True

                                tool_name = tool_call["name"]
                                args = tool_call["args"]
                                requires_approval = bool(tool_approval_profile(tool_name, args).get("requires_approval", False))
                                tool_display = self.format_tool_call_display(tool_call)

                                self._trace_tool_start(run_id, tool_call)
                                self._audit_tool_request(
                                    tool_call=tool_call,
                                    user_id=user_id,
                                    user_role=user_role,
                                    session=session,
                                    run_id=run_id,
                                    status="approval_required" if requires_approval else "requested",
                                )

                                yield {
                                    "event": "tool_call_start",
                                    "data": {
                                        "tool_call_id": tool_call["id"],
                                        "tool_name": tool_name,
                                        "args": args,
                                        "display": tool_display,
                                        "requires_approval": requires_approval,
                                    }
                                }

                                if requires_approval:
                                    only_non_sensitive = False
                                    approval_request = self._build_approval_request(tool_call)
                                    current_values = self.graph.get_state(config).values or {}
                                    self.graph.update_state(
                                        config,
                                        {
                                            "approval_requests": self._append_state_item(
                                                current_values, "approval_requests", approval_request
                                            )
                                        },
                                        as_node="sre_agent",
                                    )
                                    await self.save_message(
                                        db,
                                        session.id,
                                        "assistant",
                                        content=assistant_message_content,
                                        tool_calls=[{**tool_call, "approval_request": approval_request}],
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
                                            "message": f"需要您的授权才能执行 {tool_name}",
                                            "approval_request": approval_request,
                                        }
                                    }
                                    trace_runtime.end_run(run_id, status="success")
                                    return

                            if processed_tool_call and only_non_sensitive:
                                should_auto_resume_tools = True

            # 5. 保存最终的 AI 消息
            if assistant_message_content or current_tool_calls:
                await self.save_message(
                    db,
                    session.id,
                    "assistant",
                    content=assistant_message_content,
                    tool_calls=current_tool_calls if current_tool_calls else None
                )

            trace_runtime.end_run(run_id, status="success")
            yield {
                "event": "agent_message_complete",
                "data": {"content": assistant_message_content}
            }

        except Exception as e:
            logger.error(f"Error in stream_agent_response: {e}", exc_info=True)
            if "run_id" in locals():
                trace_runtime.end_run(run_id, status="failed", error_summary=str(e))
            yield {
                "event": "error",
                "data": {"message": str(e)}
            }


# 全局单例
chat_service = ChatService()

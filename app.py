import uuid
from typing import Dict, Optional

import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from app.agent.graph import create_graph, SENSITIVE_TOOLS
from app.tools.security import before_tool_execution, after_tool_execution

PAGE_TITLE = "AutoSRE - 智能运维 Agent"
PAGE_CAPTION = "该 Agent 拥有 Docker 操作权限，请谨慎使用"

DEFAULT_USER_ROLE = "admin"

def init_session_state():
    """初始化 Streamlit 会话状态"""
    if "user_id" not in st.session_state:
        st.session_state.user_id = str(uuid.uuid4())

    if "thread_id" not in st.session_state:
        st.session_state.thread_id = st.session_state.user_id

    if "user_role" not in st.session_state:
        st.session_state.user_role = DEFAULT_USER_ROLE

    if "graph" not in st.session_state:
        st.session_state.graph = create_graph()

def get_graph_config() -> Dict:
    """获取 LangGraph 配置"""
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def render_header():
    st.title(PAGE_TITLE)
    st.caption(PAGE_CAPTION)


def render_message_history(messages):
    """
    智能渲染历史：将连续的 AI 消息（包括工具调用和结果）合并到一个消息中显示
    """
    groups = []
    current_group = []

    for msg in messages:
        if isinstance(msg, HumanMessage):
            if current_group:
                groups.append(current_group)
                current_group = []
            groups.append([msg])
        else:
            current_group.append(msg)

    if current_group:
        groups.append(current_group)

    for group in groups:
        first_msg = group[0]
        role = "user" if isinstance(first_msg, HumanMessage) else "assistant"

        with st.chat_message(role):
            for msg in group:
                if isinstance(msg, HumanMessage):
                    st.write(msg.content)

                elif isinstance(msg, AIMessage):
                    if msg.tool_calls:
                        for tool_call in msg.tool_calls:
                            with st.expander(f"🛠️ AI 请求调用: {tool_call['name']}", expanded=False):
                                st.caption(f"Call ID: {tool_call['id']}")
                                st.json(tool_call['args'])

                    if msg.content:
                        st.markdown(msg.content)

                elif isinstance(msg, ToolMessage):
                    with st.expander(f"📦 工具输出: {msg.name}", expanded=False):
                        st.code(msg.content)


def render_approval_box(tool_call: Dict, app: CompiledStateGraph, config: Dict):
    """渲染高危操作审批界面"""
    with st.container(border=True):
        st.warning(f"⚠️ **高危操作审批**")
        st.markdown(f"AI 请求执行: `{tool_call['name']}`")
        with st.expander("查看详细参数", expanded=True):
            st.json(tool_call['args'])

        col1, col2 = st.columns(2)

        with col1:
            if st.button("✅ 批准执行", type="primary", key="btn_approve"):
                # 批准：继续运行
                run_agent(app, None, config)
                st.rerun()

        with col2:
            if st.button("❌ 拒绝操作", key="btn_deny"):
                # 拒绝：注入一条拒绝的 ToolMessage，并通知 AI
                deny_msg = ToolMessage(
                    tool_call_id=tool_call['id'],
                    content=f"User denied the operation {tool_call['name']}.",
                    name=tool_call['name']
                )
                app.update_state(config, {"messages": [deny_msg]}, as_node="tools")
                run_agent(app, None, config)
                st.rerun()



def run_agent(app: CompiledStateGraph, inputs: Optional[Dict], config: Dict):
    """ 运行 Agent """
    user_id = st.session_state.user_id
    user_role = st.session_state.user_role

    with st.chat_message("assistant"):
        with st.status("🤖 AI 正在思考与执行...", expanded=True) as status_box:
            final_response = ""
            events = app.stream(inputs, config=config, stream_mode="values")

            for event in events:
                if "messages" in event:
                    curr_msg = event["messages"][-1]

                    # --- AI 决定调用工具 ---
                    if isinstance(curr_msg, AIMessage) and curr_msg.tool_calls:
                        for tool_call in curr_msg.tool_calls:

                            # 执行前安全检查与日志
                            before_tool_execution(
                                tool_name=tool_call["name"],
                                args=tool_call["args"],
                                user_id=user_id,
                                user_role=user_role
                            )

                            st.write(f"🛠️ **计划调用工具**: `{tool_call['name']}`")
                            with st.expander("查看参数细节"):
                                st.json(tool_call['args'])

                    # --- 工具执行完毕返回结果 ---
                    elif isinstance(curr_msg, ToolMessage):
                        after_tool_execution(
                            tool_name=curr_msg.name,
                            result=curr_msg.content,
                            user_id=user_id,
                            user_role=user_role
                        )

                        st.write(f"✅ **工具执行完成**: `{curr_msg.name}`")
                        with st.expander("查看工具输出结果", expanded=False):
                            st.code(curr_msg.content[:1000])

                    # --- AI 生成最终回复 ---
                    elif isinstance(curr_msg, AIMessage) and curr_msg.content:
                        final_response = curr_msg.content

            # 循环结束后，更新状态盒子的标题
            status_box.update(label="✅ 执行完毕", state="complete", expanded=False)
        if final_response:
            st.markdown(final_response)


def handle_interruption(app: CompiledStateGraph, config: Dict):
    """
    检查当前图表状态是否处于断点（工具调用前），处理自动执行或展示审批
    """
    current_state = app.get_state(config)

    # LangGraph 中断点检查：如果有 next 且指向 tools 节点
    if current_state.next and current_state.next[0] == "tools":
        last_msg = current_state.values["messages"][-1]

        if not (isinstance(last_msg, AIMessage) and last_msg.tool_calls):
            return

        tool_call = last_msg.tool_calls[0]
        tool_name = tool_call["name"]

        # 高危工具需审批，普通工具自动放行
        if tool_name in SENSITIVE_TOOLS:
            render_approval_box(tool_call, app, config)
        else:
            with st.spinner(f"正在自动执行安全操作: {tool_name}..."):
                run_agent(app, None, config)
                st.rerun()


def main():
    # 初始化
    init_session_state()
    app = st.session_state.graph
    config = get_graph_config()

    # 渲染头部
    render_header()

    # 获取并渲染历史记录
    current_state = app.get_state(config)
    all_messages = current_state.values.get("messages", [])
    render_message_history(all_messages)

    # 处理中断/审批逻辑
    handle_interruption(app, config)

    # 处理新用户输入
    is_blocked = current_state.next and current_state.next[0] == "tools"

    if not is_blocked:
        if prompt := st.chat_input("输入运维指令..."):
            with st.chat_message("user"):
                st.write(prompt)

            inputs = {
                "user_role": DEFAULT_USER_ROLE,
                "messages": [HumanMessage(content=prompt)],
                "mode": "manual"
            }
            run_agent(app, inputs, config)
            st.rerun()

if __name__ == "__main__":
    main()

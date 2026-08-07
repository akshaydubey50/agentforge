import os

import requests
import streamlit as st

API_URL = os.environ.get("AGENTSYS_API_URL", "http://localhost:8100")

st.set_page_config(page_title="Agent Orchestration System", layout="wide")
st.title("Agent Orchestration System")


def api_get(path: str, **params):
    response = requests.get(f"{API_URL}{path}", params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def api_post(path: str, json_body: dict):
    response = requests.post(f"{API_URL}{path}", json=json_body, timeout=15)
    response.raise_for_status()
    return response.json()


STATUS_COLOR = {
    "pending": "⚪",
    "running": "🔵",
    "awaiting_approval": "🟠",
    "completed": "🟢",
    "failed": "🔴",
}

tab_new, tab_tasks, tab_approvals, tab_trace, tab_memory, tab_analytics = st.tabs(
    ["New Task", "Tasks", "Approvals", "Trace Explorer", "Long-Term Memory", "Analytics"]
)

with tab_new:
    st.subheader("Submit a new task")
    try:
        tools = api_get("/v1/tools")["tools"]
        st.caption("Available tools: " + ", ".join(t["name"] for t in tools))
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")
        tools = []

    request_text = st.text_area(
        "What should the agent do?",
        placeholder="Look up Acme Robotics revenue for 2026-Q2 in our metrics database and tell me if it grew from Q1.",
        height=100,
    )
    if st.button("Submit task", type="primary") and request_text:
        try:
            task = api_post("/v1/tasks", {"request_text": request_text})
            st.success(f"Task {task['id']} submitted — check the Tasks tab for progress.")
        except requests.RequestException as e:
            st.error(f"Failed to submit: {e}")

with tab_tasks:
    st.subheader("Recent tasks")
    if st.button("Refresh", key="refresh_tasks"):
        st.rerun()
    try:
        tasks = api_get("/v1/tasks", limit=30)
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")
        tasks = []

    for task in tasks:
        icon = STATUS_COLOR.get(task["status"], "⚪")
        with st.expander(f"{icon} [{task['status']}] {task['request_text'][:80]}"):
            detail = api_get(f"/v1/tasks/{task['id']}")
            st.write(f"**Final output:** {detail['final_output'] or '(not yet)'}")
            st.write("**Subtasks:**")
            for s in detail["subtasks"]:
                sicon = {"done": "✅", "escalated": "🟠", "failed": "❌", "running": "🔵"}.get(s["status"], "⚪")
                st.write(f"{sicon} [{s['position']}] {s['description']} — tool: `{s['assigned_tool'] or 'none'}` — attempts: {s['attempt_count']}")
                if s["output"]:
                    st.code(s["output"][:400], language=None)

with tab_approvals:
    st.subheader("Pending human approvals")
    if st.button("Refresh", key="refresh_approvals"):
        st.rerun()
    try:
        escalations = api_get("/v1/escalations", status="pending")
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")
        escalations = []

    if not escalations:
        st.info("No pending escalations.")

    for esc in escalations:
        with st.container(border=True):
            st.write(f"**Task:** `{esc['task_id']}`")
            if esc["subtask_id"]:
                st.write(f"**Subtask:** `{esc['subtask_id']}`")
            else:
                st.write("**Scope:** plan-level (no specific subtask)")
            st.write(f"**Reason:** {esc['reason']}")

            note = st.text_input("Decision note", key=f"note_{esc['id']}")
            override = None
            if esc["subtask_id"]:
                override = st.text_input("Override output (only used for Take Over)", key=f"override_{esc['id']}")

            cols = st.columns(3)
            if cols[0].button("✅ Approve", key=f"approve_{esc['id']}"):
                api_post(f"/v1/escalations/{esc['id']}/decide", {"decision": "approve", "note": note, "decided_by": "dashboard-user"})
                st.rerun()
            if cols[1].button("❌ Reject", key=f"reject_{esc['id']}"):
                api_post(f"/v1/escalations/{esc['id']}/decide", {"decision": "reject", "note": note, "decided_by": "dashboard-user"})
                st.rerun()
            if esc["subtask_id"] and cols[2].button("✋ Take Over", key=f"takeover_{esc['id']}"):
                if not override:
                    st.error("Provide an override output first.")
                else:
                    api_post(
                        f"/v1/escalations/{esc['id']}/decide",
                        {"decision": "take_over", "note": note, "decided_by": "dashboard-user", "override_output": override},
                    )
                    st.rerun()

with tab_trace:
    st.subheader("Trace explorer")
    try:
        all_tasks = api_get("/v1/tasks", limit=30)
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")
        all_tasks = []

    if all_tasks:
        options = {f"[{t['status']}] {t['request_text'][:60]} ({t['id'][:8]})": t["id"] for t in all_tasks}
        selected = st.selectbox("Task", list(options.keys()))
        task_id = options[selected]

        spans = api_get(f"/v1/tasks/{task_id}/trace")
        st.caption(f"{len(spans)} spans")
        for s in spans:
            duration = ""
            if s["ended_at"]:
                duration = f" ({s['status']})"
            with st.expander(f"[{s['span_type']}] {s['name']}{duration}"):
                st.write("**Input:**")
                st.json(s["input"])
                st.write("**Output:**")
                st.json(s["output"])

with tab_memory:
    st.subheader("Long-term memory (episodic summaries, facts, preferences)")
    try:
        memories = api_get("/v1/memory", limit=30)
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")
        memories = []

    for m in memories:
        st.write(f"**[{m['kind']}]** (importance {m['importance']}) {m['content']}")

with tab_analytics:
    st.subheader("Cost & performance analytics")
    try:
        stats = api_get("/v1/analytics")
    except requests.RequestException as e:
        st.error(f"Could not reach API at {API_URL}: {e}")
        stats = None

    if stats:
        cols = st.columns(4)
        cols[0].metric("Total tasks", stats["total_tasks"])
        cols[1].metric("Total tool calls", stats["total_tool_calls"])
        cols[2].metric("Escalations", sum(stats["escalations_by_status"].values()))
        cols[3].metric("Total LLM cost", f"${stats['total_cost_usd']:.4f}")

        st.write("**Tasks by status:**", stats["tasks_by_status"])
        st.write("**Escalations by resolution:**", stats["escalations_by_status"])
        st.write("**Cost by purpose:**", stats["cost_by_purpose"])

        st.write("**Per-tool performance:**")
        for tool_name, tstats in stats["tool_stats"].items():
            st.write(
                f"- `{tool_name}`: {tstats['calls']} calls, "
                f"{tstats['success_rate'] * 100:.0f}% success rate, "
                f"{tstats['avg_latency_ms']}ms avg latency"
            )

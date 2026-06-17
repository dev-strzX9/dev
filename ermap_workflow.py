"""ER MAP workflow: extract, repair identifiers, data load, and HITL selection."""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ermap_agent import (
    AgentState,
    extract_entities_node,
    repair_task_identifiers_node,
)
from ermap_data_load import DATA_LOAD_NODE
from ermap_selection import (
    ErmapQueryRow,
    format_query_results_message,
    resolve_user_selection,
    rows_from_dicts,
    rows_to_dicts,
)


def _update(*, message: str, **fields: Any) -> Dict[str, Any]:
    return {"message": message, **fields}


def make_thread_id(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def make_config(user_id: str, chat_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": make_thread_id(user_id, chat_id)}}


def is_awaiting_resume(graph: Any, config: Dict[str, Any]) -> bool:
    return bool(graph.get_state(config).next)


def selection_node(state: AgentState) -> Dict[str, Any]:
    db_results = state.get("db_results") or []
    if not db_results:
        return _update(message="조회 결과가 없습니다.", filtered_results=[], phase="no_results")

    rows = rows_from_dicts(db_results)
    if len(rows) == 1:
        return _update(
            message="조회 결과 1건 — 자동 선택",
            filtered_results=rows_to_dicts(rows),
            phase="selected",
        )

    user_reply = interrupt(
        {
            "phase": "awaiting_selection",
            "message": format_query_results_message(rows),
            "count": len(rows),
        }
    )

    reply_text = str(user_reply).strip()
    if not reply_text:
        return _update(
            message="선택 입력이 비어 있습니다.",
            filtered_results=[],
            phase="selection_failed",
        )

    selected_rows, selection_message = resolve_user_selection(reply_text, rows)
    if not selected_rows:
        return _update(
            message=selection_message,
            filtered_results=[],
            phase="selection_failed",
        )

    return _update(
        message=selection_message,
        filtered_results=rows_to_dicts(selected_rows),
        phase="selected",
    )


def build_artifact_node(state: AgentState) -> Dict[str, Any]:
    filtered = state.get("filtered_results") or []
    if not filtered:
        return _update(
            message=state.get("message", "선택된 결과가 없습니다."),
            artifact={},
            phase=state.get("phase", "selection_failed"),
        )

    rows = [ErmapQueryRow.model_validate(item) for item in filtered]
    return _update(
        message=f"ER MAP artifact 생성 완료 ({len(rows)}건)",
        artifact={
            "kind": "ermap_render",
            "rows": [row.model_dump(exclude_none=True) for row in rows],
            "source_query": state.get("user_query"),
        },
        phase="completed",
    )


def _route_after_data_load(state: AgentState) -> str:
    if state.get("db_results"):
        return "selection"
    return "end"


checkpointer = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("repair_task_identifiers", repair_task_identifiers_node)
workflow.add_node("data_load", DATA_LOAD_NODE)
workflow.add_node("selection", selection_node)
workflow.add_node("build_artifact", build_artifact_node)

workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", "repair_task_identifiers")
workflow.add_edge("repair_task_identifiers", "data_load")
workflow.add_conditional_edges(
    "data_load",
    _route_after_data_load,
    {"selection": "selection", "end": END},
)
workflow.add_edge("selection", "build_artifact")
workflow.add_edge("build_artifact", END)

graph = workflow.compile(checkpointer=checkpointer)


def invoke_extraction(
    user_query: str,
    *,
    reference_date: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: AgentState = {"user_query": user_query, "phase": "started"}
    if reference_date:
        payload["reference_date"] = reference_date
    return graph.invoke(payload, config or {})


def invoke_resume(graph_ref: Any, *, user_reply: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return graph_ref.invoke(Command(resume=user_reply), config)

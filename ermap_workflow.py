"""ER MAP workflow: extract, repair identifiers, API query, and HITL selection."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ermap_agent import (
    AgentState,
    extract_entities_node,
    repair_task_identifiers_node,
)
from ermap_selection import (
    ErmapQueryRow,
    format_query_results_message,
    query_ermap_api,
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


def db_query_node(state: AgentState) -> Dict[str, Any]:
    tasks = (state.get("extracted_entities") or {}).get("tasks") or []
    if not tasks:
        return _update(message="조회할 task가 없습니다.", query_results=[], phase="no_results")

    try:
        rows = query_ermap_api(tasks, api_url=os.environ.get("ERMAP_QUERY_API_URL"))
    except Exception as exc:
        return _update(
            message=f"ER MAP API 조회 실패: {exc}",
            query_results=[],
            phase="no_results",
        )

    if not rows:
        return _update(message="조회 결과가 없습니다.", query_results=[], phase="no_results")

    return _update(
        message=f"DB 조회 완료 ({len(rows)}건)",
        query_results=rows_to_dicts(rows),
        phase="queried",
    )


def selection_node(state: AgentState) -> Dict[str, Any]:
    rows = rows_from_dicts(state.get("query_results") or [])
    if not rows:
        return _update(message="조회 결과가 없습니다.", filtered_results=[], phase="no_results")

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

    row = ErmapQueryRow.model_validate(filtered[0])
    return _update(
        message="ER MAP artifact 생성 완료",
        artifact={
            "kind": "ermap_render",
            "row": row.model_dump(exclude_none=True),
            "source_query": state.get("user_query"),
        },
        phase="completed",
    )


def _route_after_db_query(state: AgentState) -> str:
    if state.get("query_results"):
        return "selection"
    return "end"


checkpointer = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("repair_task_identifiers", repair_task_identifiers_node)
workflow.add_node("db_query", db_query_node)
workflow.add_node("selection", selection_node)
workflow.add_node("build_artifact", build_artifact_node)

workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", "repair_task_identifiers")
workflow.add_edge("repair_task_identifiers", "db_query")
workflow.add_conditional_edges(
    "db_query",
    _route_after_db_query,
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

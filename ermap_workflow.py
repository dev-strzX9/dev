"""ER MAP workflow: extract tasks, query API, human-in-the-loop selection."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ermap_agent import extract_entities_node
from ermap_selection import (
    ErmapQueryRow,
    format_query_results_message,
    query_ermap_api,
    resolve_user_selection,
    rows_from_dicts,
    rows_to_dicts,
)


class WorkflowState(TypedDict, total=False):
    user_query: str
    reference_date: str
    extracted_entities: Dict[str, Any]
    query_results: List[Dict[str, Any]]
    filtered_results: List[Dict[str, Any]]
    artifact: Dict[str, Any]
    phase: str
    message: str


def _build_state_update(*, message: str, **fields: Any) -> Dict[str, Any]:
    return {"message": message, **fields}


def make_thread_id(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def make_config(user_id: str, chat_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": make_thread_id(user_id, chat_id)}}


def is_awaiting_resume(graph: Any, config: Dict[str, Any]) -> bool:
    return bool(graph.get_state(config).next)


def db_query_node(state: WorkflowState) -> Dict[str, Any]:
    tasks = (state.get("extracted_entities") or {}).get("tasks") or []
    if not tasks:
        return _build_state_update(
            message="조회할 task가 없습니다.",
            query_results=[],
            phase="no_results",
        )

    try:
        rows = query_ermap_api(
            tasks,
            api_url=os.environ.get("ERMAP_QUERY_API_URL"),
        )
    except Exception as exc:
        return _build_state_update(
            message=f"ER MAP API 조회 실패: {exc}",
            query_results=[],
            phase="no_results",
        )

    if not rows:
        return _build_state_update(
            message="조회 결과가 없습니다.",
            query_results=[],
            phase="no_results",
        )

    return _build_state_update(
        message=f"DB 조회 완료 ({len(rows)}건)",
        query_results=rows_to_dicts(rows),
        phase="queried",
    )


def selection_node(state: WorkflowState) -> Dict[str, Any]:
    rows = rows_from_dicts(state.get("query_results") or [])
    if not rows:
        return _build_state_update(
            message="조회 결과가 없습니다.",
            filtered_results=[],
            phase="no_results",
        )

    if len(rows) == 1:
        return _build_state_update(
            message="조회 결과 1건 — 자동 선택했습니다.",
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
        return _build_state_update(
            message="선택 입력이 비어 있습니다.",
            filtered_results=[],
            phase="selection_failed",
        )

    selected_rows, selection_message = resolve_user_selection(reply_text, rows)
    if not selected_rows:
        return _build_state_update(
            message=selection_message,
            filtered_results=[],
            phase="selection_failed",
        )

    return _build_state_update(
        message=selection_message,
        filtered_results=rows_to_dicts(selected_rows),
        phase="selected",
    )


def build_artifact_node(state: WorkflowState) -> Dict[str, Any]:
    filtered = state.get("filtered_results") or []
    if not filtered:
        return _build_state_update(
            message=state.get("message", "선택된 결과가 없습니다."),
            artifact={},
            phase=state.get("phase", "selection_failed"),
        )

    row = ErmapQueryRow.model_validate(filtered[0])
    return _build_state_update(
        message="ER MAP 렌더용 artifact 생성 완료",
        artifact={
            "kind": "ermap_render",
            "row": row.model_dump(exclude_none=True),
            "source_query": state.get("user_query"),
        },
        phase="completed",
    )


def _route_after_db_query(state: WorkflowState) -> str:
    if state.get("query_results"):
        return "selection"
    return "end"


def invoke_new_query(
    graph: Any,
    *,
    user_query: str,
    reference_date: Optional[str] = None,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    payload: WorkflowState = {"user_query": user_query, "phase": "started"}
    if reference_date:
        payload["reference_date"] = reference_date
    return graph.invoke(payload, config)


def invoke_resume(graph: Any, *, user_reply: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return graph.invoke(Command(resume=user_reply), config)


checkpointer = MemorySaver()

workflow = StateGraph(WorkflowState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("db_query", db_query_node)
workflow.add_node("selection", selection_node)
workflow.add_node("build_artifact", build_artifact_node)

workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", "db_query")
workflow.add_conditional_edges(
    "db_query",
    _route_after_db_query,
    {"selection": "selection", "end": END},
)
workflow.add_edge("selection", "build_artifact")
workflow.add_edge("build_artifact", END)

graph = workflow.compile(checkpointer=checkpointer)

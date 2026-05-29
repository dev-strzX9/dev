"""Two-turn chat workflow: extract → (your DB) → list → user picks → filter."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ermap_agent import graph as extract_graph
from ermap_selection import (
    filter_candidates_by_user_choice,
    format_candidates_message,
    selection_graph,
)


def run_extraction(
    user_query: str,
    reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """1턴: 자연어 → tasks."""

    payload: Dict[str, Any] = {"user_query": user_query}
    if reference_date:
        payload["reference_date"] = reference_date
    return extract_graph.invoke(payload)


def run_selection_filter(
    query_results: List[Dict[str, Any]],
    user_selection: str,
) -> Dict[str, Any]:
    """2턴: DB rows + 사용자 답변 → filtered_results."""

    return selection_graph.invoke(
        {
            "query_results": query_results,
            "user_selection": user_selection,
        }
    )


def build_selection_turn_message(query_results: List[Dict[str, Any]]) -> str:
    """2턴 질문 전, 채팅에 보여줄 번호 목록."""

    return format_candidates_message(query_results)

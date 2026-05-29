"""Chat-based selection over DB rows (no custom UI)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from ermap_agent import DEFAULT_MAX_RETRIES, DEFAULT_MODEL, DEFAULT_TIMEOUT_SEC

_PROMPT_PATH = Path(__file__).with_name("ermap_selection_prompt.yaml")

_INDEX_ONLY = re.compile(
    r"^\s*(?:"
    r"(\d+)\s*번?|"
    r"번호\s*(\d+)|"
    r"index\s*(\d+)|"
    r"#\s*(\d+)"
    r")\s*\.?\s*$",
    re.IGNORECASE,
)


class SelectionChoice(BaseModel):
    """LLM-parsed user choice over numbered candidates."""

    selected_indices: List[int] = Field(
        default_factory=list,
        description="후보 목록 번호(1부터 시작). 복수 선택 가능. 해당 없으면 빈 리스트.",
    )
    summary: Optional[str] = Field(
        default=None,
        description="사용자가 고른 항목을 한 줄로 요약 (선택)",
    )


class SelectionState(TypedDict, total=False):
    query_results: List[Dict[str, Any]]
    user_selection: str
    filtered_results: List[Dict[str, Any]]
    selected_indices: List[int]
    message: str


def _load_system_prompt() -> str:
    config = yaml.safe_load(_PROMPT_PATH.read_text(encoding="utf-8"))
    return config["system_prompt"]


def _compact_candidate(index: int, row: Dict[str, Any]) -> Dict[str, Any]:
    """토큰 절약용 — index + 주요 필드만 LLM에 전달."""

    preferred_keys = (
        "id",
        "ermap_id",
        "lot_id",
        "eqp_id",
        "chamber_id",
        "lot_slot_id",
        "slot",
        "step",
        "ermap_type",
        "measure_time",
        "created_at",
        "file_path",
        "wafer_id",
    )
    compact: Dict[str, Any] = {"index": index}
    for key in preferred_keys:
        if key in row and row[key] is not None:
            compact[key] = row[key]
    if len(compact) == 1:
        compact["data"] = row
    return compact


def format_candidates_message(
    candidates: List[Dict[str, Any]],
    *,
    header: str = "조회 결과가 여러 건입니다. 번호로 선택해 주세요.",
    max_items: int = 30,
) -> str:
    """채팅에 붙여 넣을 번호 목록 텍스트 (UI 대신)."""

    if not candidates:
        return "조회 결과가 없습니다."

    if len(candidates) == 1:
        return "조회 결과가 1건입니다. '1번' 또는 '확인'이라고 답하면 됩니다.\n\n" + _format_one(
            1, candidates[0]
        )

    lines = [header, ""]
    shown = candidates[:max_items]
    for i, row in enumerate(shown, start=1):
        lines.append(_format_one(i, row))

    if len(candidates) > max_items:
        lines.append(f"\n... 외 {len(candidates) - max_items}건 (앞 {max_items}건만 표시)")
    lines.append("\n예: `1번`, `2번과 3번`, `EKE0104 있는 쪽`")
    return "\n".join(lines)


def _format_one(index: int, row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "lot_id",
        "eqp_id",
        "chamber_id",
        "lot_slot_id",
        "slot",
        "step",
        "ermap_type",
        "measure_time",
        "created_at",
        "id",
    ):
        if row.get(key) is not None:
            parts.append(f"{key}={row[key]}")
    if not parts:
        parts.append(json.dumps(row, ensure_ascii=False)[:200])
    return f"{index}. " + ", ".join(parts)


def _parse_index_only(user_message: str) -> Optional[List[int]]:
    match = _INDEX_ONLY.match(user_message.strip())
    if not match:
        return None
    for group in match.groups():
        if group:
            return [int(group)]
    return None


def _parse_simple_multi_indices(user_message: str, max_index: int) -> Optional[List[int]]:
    """예: 1번 3번, 2,4"""

    text = user_message.strip().lower()
    found = re.findall(r"(\d+)\s*번?", text)
    if not found:
        return None
    indices = []
    for token in found:
        idx = int(token)
        if 1 <= idx <= max_index:
            indices.append(idx)
    return sorted(set(indices)) if indices else None


def filter_candidates_by_user_choice(
    candidates: List[Dict[str, Any]],
    user_message: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout: int = DEFAULT_TIMEOUT_SEC,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> Dict[str, Any]:
    """
    DB 조회 결과 + 사용자 채팅 답변 → 선택된 row만 반환.

    Returns:
        filtered_results: 선택된 원본 row dict 목록
        selected_indices: 1-based index 목록
        message: 사용자에게 보여줄 요약
    """

    if not candidates:
        return {
            "filtered_results": [],
            "selected_indices": [],
            "message": "조회 결과가 없습니다.",
        }

    if len(candidates) == 1:
        text = user_message.strip().lower()
        if text in {"", "1", "1번", "확인", "ok", "yes", "네", "예"}:
            return {
                "filtered_results": [candidates[0]],
                "selected_indices": [1],
                "message": "1건을 선택했습니다.",
            }

    max_index = len(candidates)

    quick = _parse_index_only(user_message)
    if quick and all(1 <= i <= max_index for i in quick):
        picked = [candidates[i - 1] for i in quick]
        return {
            "filtered_results": picked,
            "selected_indices": quick,
            "message": f"{len(picked)}건을 선택했습니다.",
        }

    multi = _parse_simple_multi_indices(user_message, max_index)
    if multi:
        picked = [candidates[i - 1] for i in multi]
        return {
            "filtered_results": picked,
            "selected_indices": multi,
            "message": f"{len(picked)}건을 선택했습니다.",
        }

    compact_list = [_compact_candidate(i, row) for i, row in enumerate(candidates, start=1)]
    system_prompt = _load_system_prompt()
    human_payload = {
        "candidates": compact_list,
        "user_message": user_message,
    }

    llm = ChatOpenAI(
        model=model,
        temperature=0,
        timeout=timeout,
        max_retries=max_retries,
    )
    parsed = llm.with_structured_output(SelectionChoice, method="function_calling").invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=json.dumps(human_payload, ensure_ascii=False)),
        ]
    )

    if isinstance(parsed, SelectionChoice):
        indices = parsed.selected_indices
        summary = parsed.summary
    elif hasattr(parsed, "model_dump"):
        data = parsed.model_dump()
        indices = data.get("selected_indices") or []
        summary = data.get("summary")
    else:
        indices = parsed.get("selected_indices", []) if isinstance(parsed, dict) else []
        summary = parsed.get("summary") if isinstance(parsed, dict) else None

    valid = sorted({i for i in indices if isinstance(i, int) and 1 <= i <= max_index})
    if not valid:
        return {
            "filtered_results": [],
            "selected_indices": [],
            "message": "선택을 이해하지 못했습니다. 번호(예: 1번)로 다시 알려주세요.",
        }

    picked = [candidates[i - 1] for i in valid]
    msg = summary or f"{len(picked)}건을 선택했습니다."
    return {
        "filtered_results": picked,
        "selected_indices": valid,
        "message": msg,
    }


def filter_selection_node(state: SelectionState) -> Dict[str, Any]:
    """LangGraph 노드 — state에 query_results, user_selection 필요."""

    candidates = state.get("query_results") or []
    user_message = state.get("user_selection") or ""
    result = filter_candidates_by_user_choice(candidates, user_message)
    return {
        "filtered_results": result["filtered_results"],
        "selected_indices": result["selected_indices"],
        "message": result["message"],
    }


_selection_workflow = StateGraph(SelectionState)
_selection_workflow.add_node("filter_selection", filter_selection_node)
_selection_workflow.add_edge(START, "filter_selection")
_selection_workflow.add_edge("filter_selection", END)

selection_graph = _selection_workflow.compile()

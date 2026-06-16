"""ER MAP query result models and HITL selection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml
from pydantic import BaseModel, Field

from llm_api import chat_structured

_TYPE_LABELS = {"1": "PRSTRIP", "2": "BEVEL"}

_FILTER_FIELDS = (
    ("eqp_id", "eqp_id", True),
    ("main_eqp_id", "main_eqp_id", False),
    ("eqp_recipe_id", "eqp_recipe_id", True),
    ("oper_desc", "oper_desc", True),
    ("lot_id", "lot_id", False),
    ("unit_id", "unit_id", False),
    ("type", "type", False),
    ("side_info", "side_info", False),
)


class ErmapQueryRow(BaseModel):
    """One ER MAP row returned from the DB."""

    eqp_id: str = Field(description="Chamber ID")
    main_eqp_id: str = Field(description="Equipment ID")
    eqp_recipe_id: Optional[str] = None
    oper_desc: Optional[str] = None
    lot_id: Optional[str] = None
    unit_id: Optional[str] = None
    type: Optional[str] = None
    side_info: Optional[str] = None


class ResultFilter(BaseModel):
    """LLM-parsed user selection: list numbers and/or column conditions."""

    selection_indices: List[int] = Field(
        default_factory=list,
        description="1-based list numbers. Supports multiple e.g. [1, 2, 3].",
    )
    eqp_id: Optional[str] = Field(default=None, description="Chamber ID")
    main_eqp_id: Optional[str] = Field(default=None, description="Equipment ID")
    eqp_recipe_id: Optional[str] = Field(default=None, description="Recipe ID")
    oper_desc: Optional[str] = Field(default=None, description="Operation")
    lot_id: Optional[str] = Field(default=None, description="Lot ID")
    unit_id: Optional[str] = Field(default=None, description="Slot")
    type: Optional[str] = Field(default=None, description='1=PRSTRIP, 2=BEVEL')
    side_info: Optional[str] = Field(default=None, description="front_side or backside")


def rows_to_dicts(rows: Sequence[ErmapQueryRow]) -> List[Dict[str, Any]]:
    return [row.model_dump(exclude_none=True) for row in rows]


def rows_from_dicts(raw_rows: Sequence[Dict[str, Any]]) -> List[ErmapQueryRow]:
    return [ErmapQueryRow.model_validate(row) for row in raw_rows]


def format_query_results_message(rows: Sequence[ErmapQueryRow]) -> str:
    lines = [
        f"조회 결과 {len(rows)}건입니다. 번호(복수 가능) 또는 컬럼 조건으로 선택해 주세요.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        type_label = _TYPE_LABELS.get(row.type or "", row.type or "-")
        lines.append(
            f"{index}. chamber={row.eqp_id} | equip={row.main_eqp_id} | "
            f"recipe={row.eqp_recipe_id or '-'} | oper={row.oper_desc or '-'} | "
            f"lot={row.lot_id or '-'} | slot={row.unit_id or '-'} | "
            f"type={type_label} | side={row.side_info or '-'}"
        )
    return "\n".join(lines)


def _has_column_filters(result_filter: ResultFilter) -> bool:
    return bool(
        result_filter.model_dump(exclude_none=True, exclude={"selection_indices"})
    )


def _row_matches_column_filter(row: ErmapQueryRow, result_filter: ResultFilter) -> bool:
    for filter_field, row_field, contains in _FILTER_FIELDS:
        filter_value = getattr(result_filter, filter_field)
        if filter_value is None:
            continue
        row_value = getattr(row, row_field)
        if row_value is None:
            return False
        row_text = str(row_value).strip().lower()
        filter_text = str(filter_value).strip().lower()
        if contains:
            if filter_text not in row_text:
                return False
        elif row_text != filter_text:
            return False
    return True


def apply_result_filter(
    rows: Sequence[ErmapQueryRow],
    result_filter: ResultFilter,
) -> List[ErmapQueryRow]:
    if result_filter.selection_indices:
        candidates: List[ErmapQueryRow] = []
        seen: set[int] = set()
        for index in result_filter.selection_indices:
            if index < 1:
                continue
            position = index - 1
            if position >= len(rows) or position in seen:
                continue
            seen.add(position)
            candidates.append(rows[position])
    else:
        candidates = list(rows)

    if not _has_column_filters(result_filter):
        return candidates

    return [row for row in candidates if _row_matches_column_filter(row, result_filter)]


def extract_result_filter_llm(
    user_reply: str,
    rows: Sequence[ErmapQueryRow],
) -> ResultFilter:
    prompt_path = Path(__file__).with_name("ermap_selection_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    user_content = (
        f"{format_query_results_message(rows)}\n\n"
        f"사용자 선택: {user_reply.strip()}"
    )
    parsed = chat_structured(
        system_prompt=prompt_config["system_prompt"],
        user_content=user_content,
        response_model=ResultFilter,
    )
    if isinstance(parsed, ResultFilter):
        return parsed
    return ResultFilter.model_validate(parsed.model_dump(exclude_none=True))


def resolve_user_selection(
    user_reply: str,
    rows: Sequence[ErmapQueryRow],
) -> tuple[List[ErmapQueryRow], str]:
    if not user_reply.strip():
        return [], "선택 입력이 비어 있습니다."

    result_filter = extract_result_filter_llm(user_reply, rows)
    selected = apply_result_filter(rows, result_filter)

    if not selected:
        return [], "선택 조건에 맞는 항목이 없습니다."
    if len(selected) == 1:
        return selected, "1건을 선택했습니다."
    return selected, f"{len(selected)}건을 선택했습니다."

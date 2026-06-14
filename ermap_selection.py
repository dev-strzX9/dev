"""ER MAP query row model, result filtering, and selection helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml
from pydantic import BaseModel, Field, field_validator

from llm_api import chat_structured

_ERMAP_TYPE_ALIASES = {
    "1": "1",
    "2": "2",
    "prstrip": "1",
    "pr strip": "1",
    "피알스트립": "1",
    "bevel": "2",
    "베벨": "2",
}


def _normalize_ermap_type(value: Any) -> Optional[str]:
    if value is None:
        return None
    key = str(value).strip().lower()
    if key.isdigit() and key in ("1", "2"):
        return key
    return _ERMAP_TYPE_ALIASES.get(key)

_KOREAN_ORDINALS = {
    "첫": 1,
    "첫번째": 1,
    "하나": 1,
    "두": 2,
    "두번째": 2,
    "둘": 2,
    "세": 3,
    "세번째": 3,
    "셋": 3,
    "네": 4,
    "네번째": 4,
    "넷": 4,
    "다섯": 5,
    "다섯번째": 5,
}

_ENGLISH_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
}

_SIDE_ALIASES = {
    "front": "front_side",
    "front side": "front_side",
    "front_side": "front_side",
    "frontside": "front_side",
    "전면": "front_side",
    "back": "backside",
    "back side": "backside",
    "backside": "backside",
    "후면": "backside",
}

_TYPE_LABELS = {
    "1": "PRSTRIP",
    "2": "BEVEL",
}


class ErmapQueryRow(BaseModel):
    """One ER MAP row returned from the database."""

    eqp_id: str = Field(description="Chamber ID")
    main_eqp_id: str = Field(description="Main equipment ID")
    eqp_recipe_id: Optional[str] = Field(default=None, description="Recipe ID")
    oper_desc: Optional[str] = Field(default=None, description="Operation description")
    lot_id: Optional[str] = Field(default=None, description="Lot ID")
    unit_id: Optional[str] = Field(default=None, description="Wafer slot / unit")
    type: Optional[str] = Field(default=None, description='ER MAP type "1" or "2"')
    side_info: Optional[str] = Field(
        default=None,
        description='Surface side: "front_side" or "backside"',
    )

    @field_validator("eqp_id", "main_eqp_id", "lot_id", mode="before")
    @classmethod
    def _strip_upper(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned.upper() if cleaned else value
        return value

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, value: Any) -> Any:
        return _normalize_ermap_type(value)

    @field_validator("side_info", mode="before")
    @classmethod
    def _normalize_side(cls, value: Any) -> Any:
        if value is None:
            return None
        key = str(value).strip().lower()
        return _SIDE_ALIASES.get(key, key)


class ResultFilter(BaseModel):
    """Structured filter for choosing rows from query_results."""

    selection_index: Optional[int] = Field(
        default=None,
        description="1-based index into the displayed result list",
        ge=1,
    )
    eqp_id: Optional[str] = Field(default=None, description="Chamber ID filter")
    main_eqp_id: Optional[str] = Field(default=None, description="Equipment ID filter")
    eqp_recipe_id: Optional[str] = Field(default=None, description="Recipe ID filter")
    oper_desc: Optional[str] = Field(default=None, description="Operation filter")
    lot_id: Optional[str] = Field(default=None, description="Lot ID filter")
    unit_id: Optional[str] = Field(default=None, description="Slot / unit filter")
    type: Optional[str] = Field(default=None, description='Type filter "1" or "2"')
    side_info: Optional[str] = Field(default=None, description="Side filter")

    @field_validator(
        "eqp_id",
        "main_eqp_id",
        "eqp_recipe_id",
        "lot_id",
        mode="before",
    )
    @classmethod
    def _strip_upper_filter_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned.upper() if cleaned else value
        return value

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type_filter(cls, value: Any) -> Any:
        return _normalize_ermap_type(value)

    @field_validator("side_info", mode="before")
    @classmethod
    def _normalize_side_filter(cls, value: Any) -> Any:
        if value is None:
            return None
        key = str(value).strip().lower()
        return _SIDE_ALIASES.get(key, key)


def rows_to_dicts(rows: Sequence[ErmapQueryRow]) -> List[Dict[str, Any]]:
    return [row.model_dump(exclude_none=True) for row in rows]


def rows_from_dicts(raw_rows: Sequence[Dict[str, Any]]) -> List[ErmapQueryRow]:
    return [ErmapQueryRow.model_validate(row) for row in raw_rows]


def task_to_db_params(task: Dict[str, Any]) -> Dict[str, Any]:
    """Map extraction task fields to database column names."""

    return {
        "main_eqp_id": task.get("eqp_id"),
        "eqp_id": task.get("chamber_id"),
        "lot_id": task.get("lot_id"),
        "unit_id": task.get("slot"),
        "start_date": task.get("start_date"),
        "end_date": task.get("end_date"),
    }


def _type_label(type_code: Optional[str]) -> str:
    if not type_code:
        return "-"
    return _TYPE_LABELS.get(type_code, type_code)


def format_query_results_message(rows: Sequence[ErmapQueryRow]) -> str:
    lines = [
        f"조회 결과 {len(rows)}건입니다. 번호를 고르거나 조건으로 말씀해 주세요.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        lines.append(
            f"{index}. chamber={row.eqp_id} | equip={row.main_eqp_id} | "
            f"recipe={row.eqp_recipe_id or '-'} | oper={row.oper_desc or '-'} | "
            f"lot={row.lot_id or '-'} | slot={row.unit_id or '-'} | "
            f"type={_type_label(row.type)} | side={row.side_info or '-'}"
        )
    return "\n".join(lines)


def parse_selection_index(text: str) -> Optional[int]:
    """Parse 1-based list index from user text without calling the LLM."""

    cleaned = text.strip()
    if not cleaned:
        return None

    digit_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:번|번째)?(?!\d)", cleaned)
    if digit_match:
        return int(digit_match.group(1))

    lowered = cleaned.lower()
    for token, index in _ENGLISH_ORDINALS.items():
        if re.search(rf"\b{re.escape(token)}\b", lowered):
            return index

    compact = re.sub(r"\s+", "", cleaned)
    for token, index in _KOREAN_ORDINALS.items():
        if token in compact:
            return index

    return None


def _normalize_compare_text(value: Any) -> str:
    return str(value).strip().lower()


def _field_matches(row_value: Any, filter_value: Any, *, contains: bool = False) -> bool:
    if filter_value is None:
        return True
    if row_value is None:
        return False

    row_text = _normalize_compare_text(row_value)
    filter_text = _normalize_compare_text(filter_value)
    if contains:
        return filter_text in row_text
    return row_text == filter_text


def apply_result_filter(
    rows: Sequence[ErmapQueryRow],
    result_filter: ResultFilter,
) -> List[ErmapQueryRow]:
    if result_filter.selection_index is not None:
        index = result_filter.selection_index - 1
        if 0 <= index < len(rows):
            return [rows[index]]
        return []

    active_filters = result_filter.model_dump(exclude_none=True)
    active_filters.pop("selection_index", None)
    if not active_filters:
        return []

    matched: List[ErmapQueryRow] = []
    for row in rows:
        if result_filter.eqp_id and not _field_matches(
            row.eqp_id, result_filter.eqp_id, contains=True
        ):
            continue
        if result_filter.main_eqp_id and not _field_matches(
            row.main_eqp_id, result_filter.main_eqp_id
        ):
            continue
        if result_filter.eqp_recipe_id and not _field_matches(
            row.eqp_recipe_id, result_filter.eqp_recipe_id, contains=True
        ):
            continue
        if result_filter.oper_desc and not _field_matches(
            row.oper_desc, result_filter.oper_desc, contains=True
        ):
            continue
        if result_filter.lot_id and not _field_matches(row.lot_id, result_filter.lot_id):
            continue
        if result_filter.unit_id and not _field_matches(row.unit_id, result_filter.unit_id):
            continue
        if result_filter.type and not _field_matches(row.type, result_filter.type):
            continue
        if result_filter.side_info and not _field_matches(
            row.side_info, result_filter.side_info
        ):
            continue
        matched.append(row)

    return matched


def extract_result_filter_llm(user_reply: str) -> ResultFilter:
    prompt_path = Path(__file__).with_name("ermap_selection_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    system_prompt = prompt_config["system_prompt"]

    parsed = chat_structured(
        system_prompt=system_prompt,
        user_content=user_reply,
        response_model=ResultFilter,
    )

    if isinstance(parsed, ResultFilter):
        return parsed
    if hasattr(parsed, "model_dump"):
        return ResultFilter.model_validate(parsed.model_dump(exclude_none=True))
    return ResultFilter.model_validate(parsed)


def resolve_user_selection(
    user_reply: str,
    rows: Sequence[ErmapQueryRow],
) -> tuple[List[ErmapQueryRow], str]:
    """Resolve selection by index parsing first, then LLM ResultFilter."""

    index = parse_selection_index(user_reply)
    if index is not None:
        selected = apply_result_filter(rows, ResultFilter(selection_index=index))
        if selected:
            return selected, f"{index}번 항목을 선택했습니다."
        return [], f"{index}번은 목록 범위를 벗어났습니다."

    result_filter = extract_result_filter_llm(user_reply)
    if result_filter.selection_index is not None:
        selected = apply_result_filter(rows, result_filter)
        if selected:
            return selected, f"{result_filter.selection_index}번 항목을 선택했습니다."
        return [], f"{result_filter.selection_index}번은 목록 범위를 벗어났습니다."

    selected = apply_result_filter(rows, result_filter)
    if len(selected) == 1:
        return selected, "조건에 맞는 항목 1건을 선택했습니다."
    if not selected:
        return [], "조건에 맞는 항목이 없습니다. 번호나 조건을 다시 말씀해 주세요."
    return (
        [],
        f"조건에 맞는 항목이 {len(selected)}건입니다. 번호를 지정하거나 조건을 더 구체적으로 말씀해 주세요.",
    )


def mock_query_db(params: Dict[str, Any]) -> List[ErmapQueryRow]:
    """Placeholder DB query that returns deterministic sample rows."""

    chamber = (params.get("eqp_id") or "EFG4803_PM1").upper()
    equipment = (params.get("main_eqp_id") or chamber.split("_")[0]).upper()
    lot_id = (params.get("lot_id") or "N4ABC12345").upper()
    slot = params.get("unit_id") or "3"

    return [
        ErmapQueryRow(
            eqp_id=chamber,
            main_eqp_id=equipment,
            eqp_recipe_id="RCP_BEVEL_A",
            oper_desc=params.get("oper_desc") or "BEVEL MAIN",
            lot_id=lot_id,
            unit_id=slot,
            type="2",
            side_info="front_side",
        ),
        ErmapQueryRow(
            eqp_id=chamber,
            main_eqp_id=equipment,
            eqp_recipe_id="RCP_BEVEL_B",
            oper_desc="BEVEL CLEAN",
            lot_id=lot_id,
            unit_id=str(int(slot) + 1) if str(slot).isdigit() else "4",
            type="2",
            side_info="backside",
        ),
        ErmapQueryRow(
            eqp_id=chamber,
            main_eqp_id=equipment,
            eqp_recipe_id="RCP_STRIP_A",
            oper_desc="PRSTRIP MAIN",
            lot_id="N6XYZ99999",
            unit_id="1",
            type="1",
            side_info="front_side",
        ),
        ErmapQueryRow(
            eqp_id=f"{equipment}_PM2",
            main_eqp_id=equipment,
            eqp_recipe_id="RCP_BEVEL_C",
            oper_desc="BEVEL MAIN",
            lot_id=lot_id,
            unit_id=slot,
            type="2",
            side_info="front_side",
        ),
    ]

"""ER MAP DB row models, task_to_db_params mapping, API query, and selection helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import httpx
import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, field_validator

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_MAX_RETRIES = 2
DEFAULT_API_TIMEOUT_SEC = 30

_ERMAP_TYPE_ALIASES = {
    "1": "1",
    "2": "2",
    "prstrip": "1",
    "pr strip": "1",
    "피알스트립": "1",
    "bevel": "2",
    "베벨": "2",
}

_SIDE_TO_DB = {
    "front-side": "front_side",
    "front_side": "front_side",
    "front": "front_side",
    "front side": "front_side",
    "전면": "front_side",
    "back-side": "backside",
    "back_side": "backside",
    "back": "backside",
    "back side": "backside",
    "후면": "backside",
}

_KOREAN_ORDINALS = {
    "첫": 1,
    "첫번째": 1,
    "두": 2,
    "두번째": 2,
    "둘": 2,
    "세": 3,
    "세번째": 3,
    "네": 4,
    "네번째": 4,
}

_ENGLISH_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
}

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


def _normalize_ermap_type(value: Any) -> Optional[str]:
    if value is None:
        return None
    key = str(value).strip().lower()
    if key.isdigit() and key in ("1", "2"):
        return key
    return _ERMAP_TYPE_ALIASES.get(key)


def _normalize_side_info(value: Any) -> Optional[str]:
    if value is None:
        return None
    key = str(value).strip().lower().replace("_", "-")
    key = re.sub(r"\s+", " ", key)
    return _SIDE_TO_DB.get(key, str(value).strip().lower())


class ErmapQueryRow(BaseModel):
    eqp_id: str = Field(description="Chamber ID")
    main_eqp_id: str = Field(description="Equipment ID")
    eqp_recipe_id: Optional[str] = Field(default=None, description="Recipe ID")
    oper_desc: Optional[str] = Field(default=None, description="Operation description")
    lot_id: Optional[str] = Field(default=None, description="Lot ID")
    unit_id: Optional[str] = Field(default=None, description="Slot / unit")
    type: Optional[str] = Field(default=None, description='ER MAP type "1" or "2"')
    side_info: Optional[str] = Field(default=None, description="front_side or backside")

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
        return _normalize_side_info(value)


class ResultFilter(BaseModel):
    selection_index: Optional[int] = Field(default=None, ge=1)
    eqp_id: Optional[str] = None
    main_eqp_id: Optional[str] = None
    eqp_recipe_id: Optional[str] = None
    oper_desc: Optional[str] = None
    lot_id: Optional[str] = None
    unit_id: Optional[str] = None
    type: Optional[str] = None
    side_info: Optional[str] = None

    @field_validator("eqp_id", "main_eqp_id", "eqp_recipe_id", "lot_id", mode="before")
    @classmethod
    def _strip_upper_ids(cls, value: Any) -> Any:
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
        return _normalize_side_info(value)


class ErmapQueryRequest(BaseModel):
    queries: List[Dict[str, Any]] = Field(default_factory=list)


class ErmapQueryResponse(BaseModel):
    rows: List[ErmapQueryRow] = Field(default_factory=list)


def task_to_db_params(task: Dict[str, Any]) -> Dict[str, Any]:
    """Map extracted ErmapTask fields to DB / API column names."""

    params: Dict[str, Any] = {
        "main_eqp_id": task.get("eqp_id"),
        "eqp_id": task.get("chamber_id"),
        "oper_desc": task.get("step"),
        "lot_id": task.get("lot_id"),
        "unit_id": task.get("slot"),
        "type": task.get("ermap_type"),
        "side_info": _normalize_side_info(task.get("side_type")),
        "start_date": task.get("start_date"),
        "end_date": task.get("end_date"),
    }
    return {key: value for key, value in params.items() if value is not None}


def tasks_to_db_params(tasks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [task_to_db_params(task) for task in tasks]


def rows_to_dicts(rows: Sequence[ErmapQueryRow]) -> List[Dict[str, Any]]:
    return [row.model_dump(exclude_none=True) for row in rows]


def rows_from_dicts(raw_rows: Sequence[Dict[str, Any]]) -> List[ErmapQueryRow]:
    return [ErmapQueryRow.model_validate(row) for row in raw_rows]


def _mock_rows_for_params(params: Dict[str, Any]) -> List[ErmapQueryRow]:
    chamber = (params.get("eqp_id") or "EFG4803_PM1").upper()
    equipment = (params.get("main_eqp_id") or chamber.split("_")[0]).upper()
    lot_id = (params.get("lot_id") or "N4ABC12345").upper()
    slot = params.get("unit_id") or "3"

    rows = [
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
    ]

    filtered = rows
    if params.get("lot_id"):
        matched = [row for row in filtered if row.lot_id == params["lot_id"].upper()]
        if matched:
            filtered = matched
    if params.get("eqp_id"):
        matched = [row for row in filtered if row.eqp_id == params["eqp_id"].upper()]
        if matched:
            filtered = matched
    if params.get("type"):
        matched = [row for row in filtered if row.type == params["type"]]
        if matched:
            filtered = matched
    return filtered


def query_ermap_api(
    tasks: Sequence[Dict[str, Any]],
    *,
    api_url: Optional[str] = None,
    timeout_sec: float = DEFAULT_API_TIMEOUT_SEC,
    headers: Optional[Dict[str, str]] = None,
) -> List[ErmapQueryRow]:
    """Send tasks (as DB params) to the ER MAP query API and return rows."""

    queries = tasks_to_db_params(tasks)
    if not queries:
        return []

    resolved_url = api_url or os.environ.get("ERMAP_QUERY_API_URL")
    if not resolved_url:
        merged: List[ErmapQueryRow] = []
        seen: set[tuple[Any, ...]] = set()
        for params in queries:
            for row in _mock_rows_for_params(params):
                key = tuple(row.model_dump().items())
                if key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        return merged

    payload = ErmapQueryRequest(queries=queries).model_dump()
    with httpx.Client(timeout=timeout_sec) as client:
        response = client.post(resolved_url, json=payload, headers=headers or {})
        response.raise_for_status()

    body = response.json()
    if isinstance(body, dict) and "rows" in body:
        parsed = ErmapQueryResponse.model_validate(body)
        return parsed.rows

    if isinstance(body, list):
        return rows_from_dicts(body)

    raise ValueError(f"Unexpected ER MAP API response shape: {type(body)!r}")


def format_query_results_message(rows: Sequence[ErmapQueryRow]) -> str:
    lines = [
        f"조회 결과 {len(rows)}건입니다. 번호를 고르거나 조건으로 말씀해 주세요.",
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


def parse_selection_index(text: str) -> Optional[int]:
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


def apply_result_filter(
    rows: Sequence[ErmapQueryRow],
    result_filter: ResultFilter,
) -> List[ErmapQueryRow]:
    if result_filter.selection_index is not None:
        index = result_filter.selection_index - 1
        if 0 <= index < len(rows):
            return [rows[index]]
        return []

    if not result_filter.model_dump(exclude_none=True, exclude={"selection_index"}):
        return []

    matched: List[ErmapQueryRow] = []
    for row in rows:
        ok = True
        for filter_field, row_field, contains in _FILTER_FIELDS:
            filter_value = getattr(result_filter, filter_field)
            if filter_value is None:
                continue
            row_value = getattr(row, row_field)
            if row_value is None:
                ok = False
                break
            row_text = str(row_value).strip().lower()
            filter_text = str(filter_value).strip().lower()
            if contains:
                if filter_text not in row_text:
                    ok = False
                    break
            elif row_text != filter_text:
                ok = False
                break
        if ok:
            matched.append(row)
    return matched


def extract_result_filter_llm(user_reply: str) -> ResultFilter:
    prompt_path = Path(__file__).with_name("ermap_selection_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))

    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0,
        timeout=DEFAULT_TIMEOUT_SEC,
        max_retries=DEFAULT_MAX_RETRIES,
    )
    parsed = llm.with_structured_output(
        ResultFilter,
        method="function_calling",
    ).invoke(
        [
            SystemMessage(content=prompt_config["system_prompt"]),
            HumanMessage(content=user_reply),
        ]
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
    index = parse_selection_index(user_reply)
    if index is not None:
        selected = apply_result_filter(rows, ResultFilter(selection_index=index))
        if selected:
            return selected, f"{index}번 항목을 선택했습니다."
        return [], f"{index}번은 목록 범위를 벗어났습니다."

    result_filter = extract_result_filter_llm(user_reply)
    selected = apply_result_filter(rows, result_filter)
    if len(selected) == 1:
        return selected, "조건에 맞는 항목 1건을 선택했습니다."
    if not selected:
        return [], "조건에 맞는 항목이 없습니다. 번호나 조건을 다시 말씀해 주세요."
    return (
        [],
        f"조건에 맞는 항목이 {len(selected)}건입니다. 번호를 지정하거나 조건을 더 구체적으로 말씀해 주세요.",
    )

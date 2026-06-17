"""ER MAP agent: stage-1 and stage-2 entity extraction only."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from llm_api import chat_structured

DEFAULT_LOOKBACK_DAYS = 1
DATE_OUTPUT_FORMAT = "%Y-%m-%d"
DATE_INPUT_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M", "%Y/%m/%d")
REFERENCE_DATE_FORMATS = DATE_INPUT_FORMATS

Phase = Literal[
    "started",
    "extracted",
    "validated",
    "extraction_failed",
    "queried",
    "no_results",
    "awaiting_selection",
    "selected",
    "selection_failed",
    "completed",
]

_IDENTIFIER_FIELDS = ("eqp_id", "chamber_id", "lot_id", "lot_slot_id", "slot")


def _normalize_date_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    for fmt in DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime(DATE_OUTPUT_FORMAT)
        except ValueError:
            continue
    return text


def _normalize_slot_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return str(int(text))
    return text


def _normalize_lot_slot_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None

    underscore_match = re.match(r"^(.+)_(\d+)$", text)
    if underscore_match:
        lot_part = underscore_match.group(1)
        slot_part = str(int(underscore_match.group(2)))
        return f"{lot_part}_{slot_part}"

    spaced_match = re.match(r"^(.+?)\s+SLOT\s+(\d+)$", text, flags=re.IGNORECASE)
    if spaced_match:
        lot_part = spaced_match.group(1).strip().upper()
        slot_part = str(int(spaced_match.group(2)))
        return f"{lot_part}_{slot_part}"

    return text


def _coerce_lot_slot_fields(task: Dict[str, Any]) -> Dict[str, Any]:
    if task.get("slot") is not None:
        task["slot"] = _normalize_slot_number(task["slot"])

    if task.get("lot_slot_id") is not None:
        task["lot_slot_id"] = _normalize_lot_slot_id(task["lot_slot_id"])
        match = re.match(r"^(.+)_(\d+)$", task["lot_slot_id"])
        if match:
            task.setdefault("lot_id", match.group(1))
            task.setdefault("slot", match.group(2))

    lot_id = task.get("lot_id")
    slot = task.get("slot")
    if lot_id and slot and not task.get("lot_slot_id"):
        task["lot_slot_id"] = f"{lot_id}_{slot}"

    for key in ("start_date", "end_date"):
        if task.get(key) is not None:
            task[key] = _normalize_date_string(task[key])

    return task


class ErmapTask(BaseModel):
    eqp_id: Optional[str] = Field(default=None, description="장비 ID (단일)")
    chamber_id: Optional[str] = Field(default=None, description="챔버 ID (단일)")
    lot_id: Optional[str] = Field(default=None, description="Lot ID (단일)")
    lot_slot_id: Optional[str] = Field(
        default=None,
        description="Lot+Slot 결합 ID. 예: N4ABC12345_3",
    )
    slot: Optional[str] = Field(default=None, description="Wafer slot 문자열. 예: 3")
    start_date: Optional[str] = Field(
        default=None,
        description='조회 시작일 YYYY-MM-DD (예: "2024-03-24")',
    )
    end_date: Optional[str] = Field(
        default=None,
        description='조회 종료일 YYYY-MM-DD (예: "2024-03-24")',
    )

    @field_validator("eqp_id", "chamber_id", "lot_id", mode="before")
    @classmethod
    def _strip_upper_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned.upper() if cleaned else value
        return value

    @field_validator("lot_slot_id", mode="before")
    @classmethod
    def _normalize_lot_slot_id_field(cls, value: Any) -> Any:
        return _normalize_lot_slot_id(value)

    @field_validator("slot", mode="before")
    @classmethod
    def _normalize_slot_field(cls, value: Any) -> Any:
        return _normalize_slot_number(value)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _normalize_date_fields(cls, value: Any) -> Any:
        return _normalize_date_string(value)

    @model_validator(mode="after")
    def _sync_lot_slot_fields(self) -> ErmapTask:
        synced = _coerce_lot_slot_fields(self.model_dump())
        for key in ("lot_id", "slot", "lot_slot_id", "start_date", "end_date"):
            object.__setattr__(self, key, synced.get(key))
        return self


class ErmapEntities(BaseModel):
    tasks: List[ErmapTask] = Field(
        default_factory=list,
        description="실행 가능한 조회 task 목록",
    )


class ErmapTaskIdentifierRepair(BaseModel):
    eqp_id: Optional[str] = Field(default=None, description="장비 ID")
    chamber_id: Optional[str] = Field(default=None, description="챔버 ID")
    lot_id: Optional[str] = Field(default=None, description="Lot ID")
    lot_slot_id: Optional[str] = Field(
        default=None,
        description="Lot+Slot 결합 ID. 예: N4ABC12345_3",
    )
    slot: Optional[str] = Field(default=None, description="Wafer slot 문자열. 예: 3")

    @field_validator("eqp_id", "chamber_id", "lot_id", mode="before")
    @classmethod
    def _strip_upper_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned.upper() if cleaned else value
        return value

    @field_validator("lot_slot_id", mode="before")
    @classmethod
    def _normalize_lot_slot_id_field(cls, value: Any) -> Any:
        return _normalize_lot_slot_id(value)

    @field_validator("slot", mode="before")
    @classmethod
    def _normalize_slot_field(cls, value: Any) -> Any:
        return _normalize_slot_number(value)


class ErmapRepairEntities(BaseModel):
    tasks: List[ErmapTaskIdentifierRepair] = Field(
        default_factory=list,
        description="Stage-1 tasks와 동일 개수의 식별자 보정 결과",
    )


class AgentState(TypedDict, total=False):
    user_query: str
    reference_date: str
    extracted_entities: Dict[str, Any]
    db_results: List[Dict[str, Any]]
    filtered_results: List[Dict[str, Any]]
    artifact: Dict[str, Any]
    phase: Phase
    message: str


def _parse_reference_date(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.now()

    for candidate_format in REFERENCE_DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), candidate_format)
        except ValueError:
            continue

    formats = ", ".join(REFERENCE_DATE_FORMATS)
    raise ValueError(
        f"Unsupported reference_date format: {raw!r}. Expected one of: {formats}"
    )


def _apply_default_dates(
    tasks: List[Dict[str, Any]],
    reference_date: datetime,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> None:
    reference_date_text = reference_date.strftime(DATE_OUTPUT_FORMAT)

    for task in tasks:
        start_date = task.get("start_date")
        end_date = task.get("end_date")

        if start_date:
            task["start_date"] = _normalize_date_string(start_date)
        if end_date:
            task["end_date"] = _normalize_date_string(end_date)

        start_date = task.get("start_date")
        end_date = task.get("end_date")

        if start_date and not end_date:
            task["end_date"] = start_date
        elif end_date and not start_date:
            task["start_date"] = end_date
        elif not start_date and not end_date:
            default_start = reference_date - timedelta(days=lookback_days)
            task["start_date"] = default_start.strftime(DATE_OUTPUT_FORMAT)
            task["end_date"] = reference_date_text


def _safe_task_dict(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    raw = _coerce_lot_slot_fields(dict(raw))
    try:
        return ErmapTask.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        coerced = _coerce_lot_slot_fields(dict(raw))
        try:
            return ErmapTask.model_validate(coerced).model_dump(exclude_none=True)
        except ValidationError:
            return {
                key: value
                for key, value in coerced.items()
                if value is not None and key in ErmapTask.model_fields
            }


def _tasks_to_dicts(parsed: ErmapEntities) -> List[Dict[str, Any]]:
    return [
        _coerce_lot_slot_fields(task.model_dump(exclude_none=True))
        for task in parsed.tasks
    ]


def _task_has_query_identifier(task: Dict[str, Any]) -> bool:
    return bool(
        task.get("eqp_id")
        or task.get("chamber_id")
        or task.get("lot_id")
        or task.get("lot_slot_id")
        or task.get("slot")
    )


def _tasks_need_identifier_repair(tasks: List[Dict[str, Any]]) -> bool:
    return any(not _task_has_query_identifier(task) for task in tasks)


def _merge_identifier_repair(
    tasks: List[Dict[str, Any]],
    repair_tasks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    merged_tasks: List[Dict[str, Any]] = []

    for index, task in enumerate(tasks):
        merged = dict(task)
        if index < len(repair_tasks):
            repair = repair_tasks[index]
            for key in _IDENTIFIER_FIELDS:
                if not merged.get(key) and repair.get(key) is not None:
                    merged[key] = repair[key]
        merged_tasks.append(_coerce_lot_slot_fields(merged))

    return merged_tasks


def _safe_repair_task_dict(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    try:
        return ErmapTaskIdentifierRepair.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        return {
            key: value
            for key, value in raw.items()
            if value is not None and key in ErmapTaskIdentifierRepair.model_fields
        }


def _parse_repair_response(parsed: Any) -> List[Dict[str, Any]]:
    if isinstance(parsed, ErmapRepairEntities):
        return [
            _coerce_lot_slot_fields(task.model_dump(exclude_none=True))
            for task in parsed.tasks
        ]
    if hasattr(parsed, "model_dump"):
        raw_tasks = parsed.model_dump(exclude_none=True).get("tasks", [])
        return [_safe_repair_task_dict(task) for task in raw_tasks]
    if isinstance(parsed, dict):
        return [_safe_repair_task_dict(task) for task in parsed.get("tasks", [])]
    return []


def _load_repair_system_prompt() -> str:
    prompt_path = Path(__file__).with_name("ermap_repair_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    return prompt_config["system_prompt"]


def _build_repair_human_message(user_query: str, tasks: List[Dict[str, Any]]) -> str:
    return json.dumps(
        {"user_query": user_query, "tasks": tasks},
        ensure_ascii=False,
        indent=2,
    )


def extract_entities_node(state: AgentState) -> Dict[str, Any]:
    reference_date = _parse_reference_date(state.get("reference_date"))
    reference_date_text = reference_date.strftime(DATE_OUTPUT_FORMAT)

    prompt_path = Path(__file__).with_name("ermap_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    system_prompt = prompt_config["system_prompt"].replace(
        "{reference_date}",
        reference_date_text,
    )

    parsed = chat_structured(
        system_prompt=system_prompt,
        user_content=state["user_query"],
        response_model=ErmapEntities,
    )

    if isinstance(parsed, ErmapEntities):
        tasks = _tasks_to_dicts(parsed)
    elif hasattr(parsed, "model_dump"):
        raw_tasks = parsed.model_dump(exclude_none=True).get("tasks", [])
        tasks = [_safe_task_dict(task) for task in raw_tasks]
    else:
        raw_tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        tasks = [_safe_task_dict(task) for task in raw_tasks]

    tasks = [task for task in tasks if task]
    if not tasks:
        tasks = [{}]

    _apply_default_dates(tasks, reference_date)

    return {
        "reference_date": reference_date_text,
        "extracted_entities": {"tasks": tasks},
        "phase": "extracted",
        "message": "ER MAP 1차 엔티티 추출 완료",
    }


def repair_task_identifiers_node(state: AgentState) -> Dict[str, Any]:
    tasks = [
        _coerce_lot_slot_fields(dict(task))
        for task in (state.get("extracted_entities") or {}).get("tasks") or []
    ]
    if not tasks:
        return {
            "extracted_entities": {"tasks": []},
            "phase": "extraction_failed",
            "message": "추출된 태스크가 없습니다.",
        }

    if _tasks_need_identifier_repair(tasks):
        parsed = chat_structured(
            system_prompt=_load_repair_system_prompt(),
            user_content=_build_repair_human_message(state.get("user_query") or "", tasks),
            response_model=ErmapRepairEntities,
        )
        repair_tasks = _parse_repair_response(parsed)
        if len(repair_tasks) == len(tasks):
            tasks = _merge_identifier_repair(tasks, repair_tasks)

    valid_tasks = [task for task in tasks if _task_has_query_identifier(task)]
    if not valid_tasks:
        return {
            "extracted_entities": {"tasks": []},
            "phase": "extraction_failed",
            "message": (
                "조회에 필요한 식별자(장비 ID, 챔버 ID, Lot ID, Lot+Slot ID)를 "
                "찾을 수 없습니다."
            ),
        }

    dropped_count = len(tasks) - len(valid_tasks)
    message = "ER MAP 2차 식별자 보정 완료"
    if dropped_count:
        message += f" (식별자 없는 태스크 {dropped_count}건 제외)"

    return {
        "extracted_entities": {"tasks": valid_tasks},
        "phase": "validated",
        "message": message,
    }
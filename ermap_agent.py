"""ER MAP entity extraction agent (no side_type)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_MAX_RETRIES = 2
DEFAULT_LOOKBACK_DAYS = 1
DATE_FORMAT = "%Y%m%d"
REFERENCE_DATE_FORMATS = (DATE_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d")

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
        return f"{underscore_match.group(1)}_{int(underscore_match.group(2))}"

    spaced_match = re.match(r"^(.+?)\s+SLOT\s+(\d+)$", text, flags=re.IGNORECASE)
    if spaced_match:
        lot_part = spaced_match.group(1).strip().upper()
        return f"{lot_part}_{int(spaced_match.group(2))}"

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

    return task


class ErmapTask(BaseModel):
    """One executable ER MAP lookup task with one date range."""

    eqp_id: Optional[str] = Field(default=None, description="장비 ID (단일)")
    chamber_id: Optional[str] = Field(default=None, description="챔버 ID (단일)")
    lot_id: Optional[str] = Field(default=None, description="Lot ID (단일)")
    lot_slot_id: Optional[str] = Field(
        default=None,
        description="Lot+Slot 결합 ID. 예: N4ABC12345_3",
    )
    slot: Optional[str] = Field(default=None, description="Wafer slot 문자열. 예: 3")
    step: Optional[str] = Field(default=None, description="step 값")
    ermap_type: Optional[str] = Field(
        default=None,
        description='ER MAP type "1"(PRSTRIP) 또는 "2"(BEVEL)',
    )
    start_date: Optional[str] = Field(default=None, description="조회 시작 YYYYMMDD")
    end_date: Optional[str] = Field(default=None, description="조회 종료 YYYYMMDD")

    @field_validator("eqp_id", "chamber_id", "lot_id", "step", mode="before")
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

    @field_validator("ermap_type", mode="before")
    @classmethod
    def _normalize_ermap_type_field(cls, value: Any) -> Any:
        return _normalize_ermap_type(value)

    @model_validator(mode="after")
    def _sync_lot_slot_fields(self) -> ErmapTask:
        synced = _coerce_lot_slot_fields(self.model_dump())
        for key in ("lot_id", "slot", "lot_slot_id"):
            object.__setattr__(self, key, synced.get(key))
        return self


class ErmapEntities(BaseModel):
    tasks: List[ErmapTask] = Field(
        default_factory=list,
        description="실행 가능한 조회 task 목록",
    )


class AgentState(TypedDict, total=False):
    user_query: str
    reference_date: str
    extracted_entities: Dict[str, Any]
    message: str


def _parse_reference_date(raw: Optional[str]) -> datetime:
    if not raw:
        return datetime.now()

    for candidate_format in REFERENCE_DATE_FORMATS:
        try:
            return datetime.strptime(raw, candidate_format)
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
    reference_date_text = reference_date.strftime(DATE_FORMAT)

    for task in tasks:
        start_date = task.get("start_date")
        end_date = task.get("end_date")

        if start_date and not end_date:
            task["end_date"] = start_date
        elif end_date and not start_date:
            task["start_date"] = end_date
        elif not start_date and not end_date:
            default_start = reference_date - timedelta(days=lookback_days)
            task["start_date"] = default_start.strftime(DATE_FORMAT)
            task["end_date"] = reference_date_text


def _safe_task_dict(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    raw = _coerce_lot_slot_fields(dict(raw))
    try:
        return ErmapTask.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        coerced = _coerce_lot_slot_fields(dict(raw))
        coerced["ermap_type"] = _normalize_ermap_type(coerced.get("ermap_type"))
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


def _build_state_update(
    *,
    reference_date_text: str,
    tasks: List[Dict[str, Any]],
    message: str,
) -> Dict[str, Any]:
    return {
        "reference_date": reference_date_text,
        "extracted_entities": {"tasks": tasks},
        "message": message,
    }


def extract_entities_node(state: AgentState) -> Dict[str, Any]:
    reference_date = _parse_reference_date(state.get("reference_date"))
    reference_date_text = reference_date.strftime(DATE_FORMAT)

    prompt_path = Path(__file__).with_name("ermap_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    system_prompt = prompt_config["system_prompt"].replace(
        "{reference_date}",
        reference_date_text,
    )

    llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0,
        timeout=DEFAULT_TIMEOUT_SEC,
        max_retries=DEFAULT_MAX_RETRIES,
    )

    parsed = llm.with_structured_output(
        ErmapEntities,
        method="function_calling",
    ).invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_query"]),
        ]
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

    return _build_state_update(
        reference_date_text=reference_date_text,
        tasks=tasks,
        message="ER MAP 엔티티 추출 완료",
    )


workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", END)

graph = workflow.compile()

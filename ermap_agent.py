"""ER MAP agent: entity extraction, mock DB query, and human-in-the-loop selection."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ermap_llm import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_SEC, create_chat_llm
from ermap_selection import (
    ErmapQueryRow,
    format_query_results_message,
    mock_query_db,
    resolve_user_selection,
    rows_from_dicts,
    rows_to_dicts,
    task_to_db_params,
)

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
    "awaiting_selection",
    "selected",
    "no_results",
    "selection_failed",
    "completed",
]


def _normalize_date_string(value: Any) -> Optional[str]:
    """Return dates as YYYY-MM-DD (e.g. 2024-03-24)."""

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
    start_date: Optional[str] = Field(
        default=None,
        description='조회 시작일 YYYY-MM-DD (예: "2024-03-24")',
    )
    end_date: Optional[str] = Field(
        default=None,
        description='조회 종료일 YYYY-MM-DD (예: "2024-03-24")',
    )

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


_IDENTIFIER_FIELDS = ("eqp_id", "chamber_id", "lot_id", "lot_slot_id", "slot")


class ErmapTaskIdentifierRepair(BaseModel):
    """Stage-2 output: identifier fields only."""

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
    query_results: List[Dict[str, Any]]
    filtered_results: List[Dict[str, Any]]
    artifact: Dict[str, Any]
    phase: Phase
    message: str


def make_thread_id(user_id: str, chat_id: str) -> str:
    return f"{user_id}:{chat_id}"


def make_config(user_id: str, chat_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": make_thread_id(user_id, chat_id)}}


def is_awaiting_resume(graph: Any, config: Dict[str, Any]) -> bool:
    snapshot = graph.get_state(config)
    return bool(snapshot.next)


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


def _first_task(state: AgentState) -> Dict[str, Any]:
    tasks = (state.get("extracted_entities") or {}).get("tasks") or []
    if not tasks:
        return {}
    return tasks[0]


def _task_has_query_identifier(task: Dict[str, Any]) -> bool:
    """Task must have at least one of eqp_id, chamber_id, lot_id, or lot_slot_id."""

    return bool(
        task.get("eqp_id")
        or task.get("chamber_id")
        or task.get("lot_id")
        or task.get("lot_slot_id")
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


def _repair_tasks_to_dicts(parsed: ErmapRepairEntities) -> List[Dict[str, Any]]:
    return [
        _coerce_lot_slot_fields(task.model_dump(exclude_none=True))
        for task in parsed.tasks
    ]


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
        return _repair_tasks_to_dicts(parsed)
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
    payload = {
        "user_query": user_query,
        "tasks": tasks,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _repair_task_identifiers_with_llm(
    user_query: str,
    tasks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    llm = create_chat_llm()

    parsed = llm.with_structured_output(
        ErmapRepairEntities,
        method="function_calling",
    ).invoke(
        [
            SystemMessage(content=_load_repair_system_prompt()),
            HumanMessage(content=_build_repair_human_message(user_query, tasks)),
        ]
    )

    repair_tasks = _parse_repair_response(parsed)
    if len(repair_tasks) != len(tasks):
        return tasks
    return _merge_identifier_repair(tasks, repair_tasks)


def repair_task_identifiers_node(state: AgentState) -> Dict[str, Any]:
    tasks = [_coerce_lot_slot_fields(dict(task)) for task in (state.get("extracted_entities") or {}).get("tasks") or []]
    if not tasks:
        return {
            "extracted_entities": {"tasks": []},
            "phase": "extraction_failed",
            "message": "추출된 태스크가 없습니다.",
        }

    if _tasks_need_identifier_repair(tasks):
        tasks = _repair_task_identifiers_with_llm(state.get("user_query") or "", tasks)

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
    message = "태스크 식별자 검증 및 2차 LLM 보정 완료"
    if dropped_count:
        message += f" (식별자 없는 태스크 {dropped_count}건 제외)"

    return {
        "extracted_entities": {"tasks": valid_tasks},
        "phase": "validated",
        "message": message,
    }


def _route_after_repair(state: AgentState) -> str:
    if state.get("phase") == "extraction_failed":
        return "end"
    return "db_query"


def extract_entities_node(state: AgentState) -> Dict[str, Any]:
    reference_date = _parse_reference_date(state.get("reference_date"))
    reference_date_text = reference_date.strftime(DATE_OUTPUT_FORMAT)

    prompt_path = Path(__file__).with_name("ermap_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    system_prompt = prompt_config["system_prompt"].replace(
        "{reference_date}",
        reference_date_text,
    )

    llm = create_chat_llm()

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

    return {
        "reference_date": reference_date_text,
        "extracted_entities": {"tasks": tasks},
        "phase": "extracted",
        "message": "ER MAP 엔티티 추출 완료",
    }


def db_query_node(state: AgentState) -> Dict[str, Any]:
    tasks = (state.get("extracted_entities") or {}).get("tasks") or [{}]
    merged_rows: List[ErmapQueryRow] = []
    seen: set[tuple[Any, ...]] = set()

    for task in tasks:
        params = task_to_db_params(task)
        for row in mock_query_db(params):
            key = tuple(row.model_dump().items())
            if key in seen:
                continue
            seen.add(key)
            merged_rows.append(row)

    if not merged_rows:
        return {
            "query_results": [],
            "phase": "no_results",
            "message": "조회 결과가 없습니다.",
        }

    return {
        "query_results": rows_to_dicts(merged_rows),
        "phase": "queried",
        "message": f"DB 조회 완료 ({len(merged_rows)}건)",
    }


def selection_node(state: AgentState) -> Dict[str, Any]:
    rows = rows_from_dicts(state.get("query_results") or [])
    if not rows:
        return {
            "filtered_results": [],
            "phase": "no_results",
            "message": "조회 결과가 없습니다.",
        }

    if len(rows) == 1:
        return {
            "filtered_results": rows_to_dicts(rows),
            "phase": "selected",
            "message": "조회 결과 1건 — 자동 선택했습니다.",
        }

    prompt_message = format_query_results_message(rows)
    user_reply = interrupt(
        {
            "phase": "awaiting_selection",
            "message": prompt_message,
            "count": len(rows),
        }
    )

    reply_text = str(user_reply).strip()
    if not reply_text:
        return {
            "filtered_results": [],
            "phase": "selection_failed",
            "message": "선택 입력이 비어 있습니다.",
        }

    selected_rows, selection_message = resolve_user_selection(reply_text, rows)
    if not selected_rows:
        return {
            "filtered_results": [],
            "phase": "selection_failed",
            "message": selection_message,
        }

    return {
        "filtered_results": rows_to_dicts(selected_rows),
        "phase": "selected",
        "message": selection_message,
    }


def build_artifact_node(state: AgentState) -> Dict[str, Any]:
    filtered = state.get("filtered_results") or []
    if not filtered:
        return {
            "artifact": {},
            "phase": state.get("phase", "selection_failed"),
            "message": state.get("message", "선택된 결과가 없습니다."),
        }

    row = ErmapQueryRow.model_validate(filtered[0])
    artifact = {
        "kind": "ermap_render",
        "row": row.model_dump(exclude_none=True),
        "source_query": state.get("user_query"),
    }
    return {
        "artifact": artifact,
        "phase": "completed",
        "message": "ER MAP 렌더용 artifact 생성 완료",
    }


def _route_after_db_query(state: AgentState) -> str:
    if not state.get("query_results"):
        return "end"
    return "selection"


def invoke_new_query(
    graph: Any,
    *,
    user_query: str,
    reference_date: Optional[str] = None,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    payload: AgentState = {"user_query": user_query, "phase": "started"}
    if reference_date:
        payload["reference_date"] = reference_date
    return graph.invoke(payload, config)


def invoke_resume(graph: Any, *, user_reply: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return graph.invoke(Command(resume=user_reply), config)


checkpointer = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("repair_task_identifiers", repair_task_identifiers_node)
workflow.add_node("db_query", db_query_node)
workflow.add_node("selection", selection_node)
workflow.add_node("build_artifact", build_artifact_node)

workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", "repair_task_identifiers")
workflow.add_conditional_edges(
    "repair_task_identifiers",
    _route_after_repair,
    {
        "db_query": "db_query",
        "end": END,
    },
)
workflow.add_conditional_edges(
    "db_query",
    _route_after_db_query,
    {
        "selection": "selection",
        "end": END,
    },
)
workflow.add_edge("selection", "build_artifact")
workflow.add_edge("build_artifact", END)

graph = workflow.compile(checkpointer=checkpointer)

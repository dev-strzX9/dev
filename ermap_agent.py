"""ER MAP entity extraction agent (timeout / schema coercion fixes)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator, model_validator

# Defaults — override via environment or caller if needed.
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SEC = 60
DEFAULT_MAX_RETRIES = 2
DEFAULT_LOOKBACK_DAYS = 1
DATE_FORMAT = "%Y%m%d"
REFERENCE_DATE_FORMATS = (DATE_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d")

_SIDE_ALIASES = {
    "front": "front-side",
    "frontside": "front-side",
    "front-side": "front-side",
    "front_side": "front-side",
    "앞면": "front-side",
    "back": "back-side",
    "backside": "back-side",
    "back-side": "back-side",
    "back_side": "back-side",
    "뒷면": "back-side",
    "후면": "back-side",
}

_ERMAP_TYPE_ALIASES = {
    "1": "1",
    "2": "2",
    "prstrip": "1",
    "pr strip": "1",
    "피알스트립": "1",
    "bevel": "2",
    "베벨": "2",
}


class ErmapTask(BaseModel):
    """One executable ER MAP lookup task with one date range."""

    eqp_id: Optional[str] = Field(
        default=None,
        description="이 task의 장비 ID. 여러 장비는 각각 다른 task로 분리",
    )
    chamber_id: Optional[str] = Field(
        default=None,
        description="이 task의 챔버 ID. 여러 챔버는 각각 다른 task로 분리",
    )
    lot_id: Optional[str] = Field(
        default=None,
        description="이 task의 Lot ID. 여러 Lot은 각각 다른 task로 분리",
    )
    lot_slot_id: Optional[str] = Field(
        default=None,
        description="이 task의 Lot+Slot 표현. 예: N4ABC12345_03",
    )
    slot: Optional[Union[str, int]] = Field(
        default=None,
        description="단일 Wafer slot. 예: slot 3이면 '3'",
    )
    step: Optional[str] = Field(
        default=None,
        description="ER MAP 조회 대상 step",
    )
    # Relaxed types for structured output — normalized after LLM response.
    ermap_type: Optional[Union[str, int]] = Field(
        default=None,
        description="ER MAP type. PRSTRIP=1, BEVEL=2 (문자열 또는 숫자)",
    )
    side_type: Optional[str] = Field(
        default=None,
        description='front-side 또는 back-side (또는 front, FRONT, 앞면 등)',
    )
    start_date: Optional[str] = Field(
        default=None,
        description="조회 시작 YYYYMMDD. 없으면 null",
    )
    end_date: Optional[str] = Field(
        default=None,
        description="조회 종료 YYYYMMDD. 없으면 null",
    )

    @field_validator("eqp_id", "chamber_id", "lot_id", "lot_slot_id", "step", mode="before")
    @classmethod
    def _strip_upper_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned.upper() if cleaned else value
        return value

    @model_validator(mode="after")
    def _normalize_fields(self) -> ErmapTask:
        if self.slot is not None:
            self.slot = str(self.slot).strip()

        if self.ermap_type is not None:
            key = str(self.ermap_type).strip().lower()
            if key.isdigit() and key in ("1", "2"):
                self.ermap_type = key
            else:
                self.ermap_type = _ERMAP_TYPE_ALIASES.get(key)

        if self.side_type is not None:
            key = str(self.side_type).strip().lower().replace(" ", "-")
            self.side_type = _SIDE_ALIASES.get(key)

        return self


class ErmapEntities(BaseModel):
    """ER MAP lookup tasks extracted from a user query."""

    tasks: List[ErmapTask] = Field(
        default_factory=list,
        description="실행 가능한 조회 작업 목록. 단순 요청도 task 1개.",
    )


class AgentState(TypedDict, total=False):
    """LangGraph state shared across ER MAP extraction nodes."""

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


def _tasks_to_dicts(parsed: ErmapEntities) -> List[Dict[str, Any]]:
    return [task.model_dump(exclude_none=True) for task in parsed.tasks]


def extract_entities_node(state: AgentState) -> AgentState:
    """Extract ER MAP lookup tasks from the user query."""

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
        tasks = [
            ErmapTask.model_validate(task).model_dump(exclude_none=True)
            for task in raw_tasks
        ]
    else:
        raw_tasks = parsed.get("tasks", []) if isinstance(parsed, dict) else []
        tasks = [
            ErmapTask.model_validate(task).model_dump(exclude_none=True)
            for task in raw_tasks
        ]

    if not tasks:
        tasks = [{}]

    _apply_default_dates(tasks, reference_date)
    entities: Dict[str, Any] = {"tasks": tasks}

    return {
        "reference_date": reference_date_text,
        "extracted_entities": entities,
        "message": "ER MAP 엔티티 추출 완료",
    }


workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", END)

graph = workflow.compile()

"""ER MAP entity extraction agent."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TypedDict

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


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
    slot: Optional[int] = Field(
        default=None,
        description="단일 Wafer slot 번호. 예: slot 3, 3번 슬롯이면 3",
    )
    step: Optional[str] = Field(
        default=None,
        description="ER MAP 조회 대상 step. 사용자가 말한 step 값을 그대로 추출",
    )
    ermap_type: Optional[int] = Field(
        default=None,
        description=(
            "ER MAP type. PRSTRIP이면 1, BEVEL 또는 베벨이면 2로 추출"
        ),
    )
    side_type: Optional[Literal["front-side", "back-side"]] = Field(
        default=None,
        description=(
            "ER MAP side type. front, front-side, 앞면이면 front-side. "
            "back, back-side, 뒷면이면 back-side"
        ),
    )
    start_date: Optional[str] = Field(
        default=None,
        description=(
            "조회 시작 날짜. 가능하면 YYYYMMDD 형식의 문자열로 추출. "
            "사용자가 날짜를 말하지 않으면 null"
        ),
    )
    end_date: Optional[str] = Field(
        default=None,
        description=(
            "조회 종료 날짜. 가능하면 YYYYMMDD 형식의 문자열로 추출. "
            "사용자가 날짜를 말하지 않으면 null"
        ),
    )


class ErmapEntities(BaseModel):
    """ER MAP lookup tasks extracted from a user query."""

    tasks: List[ErmapTask] = Field(
        default_factory=list,
        description=(
            "사용자 요청을 실행 가능한 조회 작업 단위로 나눈 목록. "
            "단순 요청도 task 1개로 추출한다."
        ),
    )


class AgentState(TypedDict, total=False):
    """LangGraph state shared across ER MAP extraction nodes."""

    user_query: str
    reference_date: str
    extracted_entities: Dict[str, Any]
    message: str


def extract_entities_node(state: AgentState) -> AgentState:
    """Extract ER MAP lookup tasks from the user query."""

    model = "gpt-4o-mini"
    default_lookback_days = 1
    date_format = "%Y%m%d"
    reference_date_formats = (
        date_format,
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    )

    reference_date = datetime.now()
    if state.get("reference_date"):
        for candidate_format in reference_date_formats:
            try:
                reference_date = datetime.strptime(
                    state["reference_date"],
                    candidate_format,
                )
                break
            except ValueError:
                continue
        else:
            formats = ", ".join(reference_date_formats)
            raise ValueError(
                "Unsupported reference_date format: "
                f"{state['reference_date']!r}. Expected one of: {formats}"
            )

    reference_date_text = reference_date.strftime(date_format)
    prompt_path = Path(__file__).with_name("ermap_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    system_prompt = prompt_config["system_prompt"].replace(
        "{reference_date}",
        reference_date_text,
    )

    parsed = ChatOpenAI(
        model=model,
        temperature=0,
    ).with_structured_output(ErmapEntities).invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_query"]),
        ]
    )

    if hasattr(parsed, "model_dump"):
        entities = parsed.model_dump(exclude_none=True)
    else:
        entities = parsed.dict(exclude_none=True)

    tasks = entities.get("tasks") or [{}]
    for task in tasks:
        start_date = task.get("start_date")
        end_date = task.get("end_date")

        if start_date and not end_date:
            task["end_date"] = start_date
        elif end_date and not start_date:
            task["start_date"] = end_date
        elif not start_date and not end_date:
            default_start = reference_date - timedelta(days=default_lookback_days)
            task.update(
                {
                    "start_date": default_start.strftime(date_format),
                    "end_date": reference_date_text,
                }
            )

    entities["tasks"] = tasks

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

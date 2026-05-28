"""ER MAP entity extraction agent."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Literal, Optional, TypedDict

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


class ErmapEntities(BaseModel):
    """Entities required for an ER MAP lookup."""

    eqp_id: Optional[str] = Field(
        default=None,
        description=(
            "장비 ID. 숫자 1자리로 시작하거나 알파벳으로 시작하고, "
            "알파벳 3~4글자 + 숫자 3~4자리 형식. 예: 4EKE0104, EKE0104"
        ),
    )
    chamber_id: Optional[str] = Field(
        default=None,
        description=(
            "챔버 ID. 장비 ID + '_' + 알파벳 1~2개 + 선택적 숫자 1개 형식. "
            "맨 뒤 숫자가 있으면 1~8만 가능. 예: 4EKE0104_PM1, EKE0104_A"
        ),
    )
    lot_id: Optional[str] = Field(
        default=None,
        description=(
            "Lot ID. 일반 Lot은 N + 1/4/5/6 중 하나 + 알파벳 3개 + 숫자 5자리. "
            "예외 Lot은 E1T + 숫자 4자리. 예: N4ABC12345, E1T1234"
        ),
    )
    slot: Optional[int] = Field(
        default=None,
        description="Wafer slot 번호. 예: slot 3, 3번 슬롯, N4ABC12345_03이면 3",
    )
    step: Optional[str] = Field(
        default=None,
        description="ER MAP 조회 대상 step. 사용자가 말한 step 값을 그대로 추출",
    )
    ermap_type: Optional[int] = Field(
        default=None,
        description="ER MAP type. 사용자가 type 1 또는 type 2처럼 말한 경우 숫자만 추출",
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
            "사용자가 지정한 조회 시작 날짜. 가능하면 YYYYMMDD 형식의 문자열로 추출. "
            "사용자가 날짜를 말하지 않으면 null"
        ),
    )
    end_date: Optional[str] = Field(
        default=None,
        description=(
            "사용자가 지정한 조회 종료 날짜. 가능하면 YYYYMMDD 형식의 문자열로 추출. "
            "사용자가 날짜를 말하지 않으면 null"
        ),
    )


class AgentState(TypedDict, total=False):
    """LangGraph state shared across ER MAP extraction nodes."""

    user_query: str
    reference_date: str
    extracted_entities: Dict[str, Any]
    message: str


def extract_entities_node(state: AgentState) -> AgentState:
    """Extract ER MAP entities from the user query."""

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

    if not entities.get("start_date") and not entities.get("end_date"):
        start_date = reference_date - timedelta(days=default_lookback_days)
        entities.update(
            {
                "start_date": start_date.strftime(date_format),
                "end_date": reference_date_text,
            }
        )

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

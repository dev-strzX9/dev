"""ER MAP entity extraction agent.

This module builds a small LangGraph workflow that extracts ER MAP lookup
parameters from a user's natural-language query. It only extracts entities;
query execution and downstream API/database calls belong in later steps.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, Literal, Optional, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field


DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_LOOKBACK_DAYS = 1
DATE_FORMAT = "%Y%m%d"
REFERENCE_DATE_FORMATS = (
    DATE_FORMAT,
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


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


SYSTEM_PROMPT = r"""
너는 반도체 ER MAP 조회 Agent의 1단계 엔티티 추출기다.

목표:
- 사용자 Query에서 ER MAP 조회에 필요한 엔티티만 추출한다.
- 실제 조회, 실행, API 호출, DB 조회는 하지 않는다.
- 사용자가 말하지 않은 값을 절대 추측하지 않는다.
- 모르는 값은 null로 둔다.
- 출력은 반드시 주어진 Pydantic schema를 따른다.

추출 대상:
1. eqp_id
2. chamber_id
3. lot_id
4. slot
5. step
6. ermap_type
7. side_type
8. start_date
9. end_date

장비 ID 추출 규칙:
- 장비 ID는 숫자 1자리로 시작하거나 알파벳으로 시작할 수 있다.
- 숫자로 시작하는 경우: 숫자 1자리 + 알파벳 3~4글자 + 숫자 3~4자리 형식이다.
- 알파벳으로 시작하는 경우: 알파벳 3~4글자 + 숫자 3~4자리 형식이다.
- 정규표현식 기준:
  (?<![A-Z0-9_])(?:\d[A-Z]{{3,4}}|[A-Z]{{3,4}})\d{{3,4}}(?![A-Z0-9_])
- 예: 4EKE0104, 4ABC1234, EKE0104, ABC1234

챔버 ID 추출 규칙:
- 챔버 ID는 장비 ID + "_" + 알파벳 1~2개 + 선택적 숫자 1개 형식이다.
- 맨 뒤 숫자가 있을 경우 반드시 1~8만 허용한다.
- 정규표현식 기준:
  (?<![A-Z0-9_])(?:\d[A-Z]{{3,4}}|[A-Z]{{3,4}})\d{{3,4}}_[A-Z]{{1,2}}[1-8]?(?![A-Z0-9_])
- 예: 4EKE0104_A, 4EKE0104_PM8, EKE0104_A, ABC1234_PM1
- 4EKE0104_PM0, EKE0104_PM9, ABC1234_PM12는 챔버 ID로 보지 않는다.
- 챔버 ID가 있으면 chamber_id에 전체 값을 넣고, "_" 앞부분은 eqp_id로도 추출한다.

Lot ID 추출 규칙:
- 일반 Lot ID는 N으로 시작한다.
- 두 번째 문자는 숫자 1, 4, 5, 6 중 하나이다.
- 그 다음은 알파벳 대문자 3개이다.
- 마지막은 숫자 5자리이다.
- 일반 Lot 정규표현식 기준:
  N[1456][A-Z]{{3}}\d{{5}}
- 예: N1ABC12345, N4EKE12345, N5AAA00001, N6XYZ99999

예외 Lot ID 규칙:
- E1T로 시작하고 그 뒤에 숫자 4자리가 온다.
- 예외 Lot 정규표현식 기준:
  E1T\d{{4}}
- 예: E1T1234, E1T0001

최종 Lot ID 정규표현식:
(?<![A-Z0-9_])(?:N[1456][A-Z]{{3}}\d{{5}}|E1T\d{{4}})(?![A-Z0-9_])

Lot + Slot 추출 규칙:
- 사용자가 Lot ID와 slot 번호를 함께 말하면 lot_id와 slot을 각각 추출한다.
- Lot ID와 slot이 하나의 문자열로 들어와도 lot_id와 slot으로만 분리한다.
- Lot과 Slot을 합친 별도 결합 필드는 만들지 않는다.
- 예:
  "N4ABC12345 slot 3" -> lot_id=N4ABC12345, slot=3
  "N4ABC12345_03" -> lot_id=N4ABC12345, slot=3

ER MAP Type 추출 규칙:
- "type 1", "타입 1", "TYPE1"이면 ermap_type=1
- "type 2", "타입 2", "TYPE2"이면 ermap_type=2
- 사용자가 type을 말하지 않으면 null로 둔다.

Side Type 추출 규칙:
- "front", "front-side", "front side", "앞면"이면 side_type="front-side"
- "back", "back-side", "back side", "뒷면", "후면"이면 side_type="back-side"
- 사용자가 side type을 말하지 않으면 null로 둔다.

Step 추출 규칙:
- 사용자가 step, 스텝, STEP과 함께 값을 말하면 step에 그대로 추출한다.
- 예:
  "step 12" -> step="12"
  "스텝 MAIN" -> step="MAIN"

날짜 추출 규칙:
- 사용자가 명시한 조회 기간이 있으면 start_date, end_date를 추출한다.
- 가능하면 YYYYMMDD 형식의 문자열로 표준화한다.
- "오늘", "어제" 같은 상대 날짜는 현재 날짜 정보를 기준으로 해석한다.
- "24일", "5월 24일", "2026년 5월 24일"처럼 특정 하루를 말하면
  start_date와 end_date에 같은 YYYYMMDD 값을 넣는다.
- "24일부터 26일까지"처럼 기간을 말하면 start_date는 시작일, end_date는 종료일로 추출한다.
- 사용자가 날짜를 말하지 않으면 start_date, end_date는 null로 둔다.
- 기본 날짜값은 LLM이 만들지 않는다. 기본 날짜는 코드에서 처리한다.

현재 날짜:
{reference_date}

중요:
- 사용자가 말한 값만 추출한다.
- 없는 장비 ID, Lot ID, Chamber ID, Slot, Step, Type, Side Type을 만들지 않는다.
- 애매하면 null로 둔다.
"""


prompt = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        ("human", "{user_query}"),
    ]
)


def extract_entities_node(state: AgentState) -> AgentState:
    """Extract ER MAP entities from the user query."""

    reference_date = datetime.now()
    if state.get("reference_date"):
        for date_format in REFERENCE_DATE_FORMATS:
            try:
                reference_date = datetime.strptime(state["reference_date"], date_format)
                break
            except ValueError:
                continue
        else:
            formats = ", ".join(REFERENCE_DATE_FORMATS)
            raise ValueError(
                "Unsupported reference_date format: "
                f"{state['reference_date']!r}. Expected one of: {formats}"
            )

    reference_date_text = reference_date.strftime(DATE_FORMAT)
    structured_llm = ChatOpenAI(
        model=DEFAULT_MODEL,
        temperature=0,
        api_key=os.getenv("OPENAI_API_KEY"),
    ).with_structured_output(ErmapEntities)

    parsed = (prompt | structured_llm).invoke(
        {
            "user_query": state["user_query"],
            "reference_date": reference_date_text,
        }
    )

    if hasattr(parsed, "model_dump"):
        entities = parsed.model_dump(exclude_none=True)
    else:
        entities = parsed.dict(exclude_none=True)

    if not entities.get("start_date") and not entities.get("end_date"):
        start_date = reference_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
        entities.update(
            {
                "start_date": start_date.strftime(DATE_FORMAT),
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


if __name__ == "__main__":
    test_queries = [
        "EKE0104_PM1 front-side ER MAP 조회해줘",
        "N4ABC12345_03 type 1 back-side 이알맵 그려줘",
        "4EKE0104_PM8 N6XYZ99999_03 step 12 type 2 앞면 24일 ERMAP",
        "E1T1234 슬롯 5 후면 이알맵 조회",
    ]

    for query in test_queries:
        result = graph.invoke(
            {
                "user_query": query,
                "reference_date": "20260528",
            }
        )

        print("=" * 80)
        print("QUERY:", query)
        print(result["extracted_entities"])

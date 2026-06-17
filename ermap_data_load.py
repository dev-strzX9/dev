"""ER MAP DB load: query tasks and return a DataFrame."""

from __future__ import annotations

from typing import Any, Dict, Sequence

import pandas as pd

from ermap_agent import AgentState

# DB 결과 컬럼 (조회 구현 시 이 컬럼에 맞춰 채우면 됨)
ERMAP_RESULT_COLUMNS = [
    "eqp_id",         # chamber
    "main_eqp_id",    # equipment
    "eqp_recipe_id",
    "oper_desc",
    "lot_id",
    "unit_id",        # slot
    "type",
    "side_info",
    "date_time",      # YYYY-MM-DD HH:MM:SS
]


def _empty_result_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=ERMAP_RESULT_COLUMNS)


def query_ermap_dataframe(tasks: Sequence[Any]) -> pd.DataFrame:
    """추출된 tasks로 DB 조회 후 DataFrame을 반환.

    TODO: 여기에 실제 DB 조회 로직을 구현하세요.
    `tasks`는 ErmapTask 또는 dict 리스트입니다.
    결과 없으면 컬럼만 있는 빈 DataFrame.
    """

    if not tasks:
        return _empty_result_frame()

    # 실제 구현 예시:
    # df = your_db_client.fetch_dataframe(tasks)
    # if df is None or df.empty:
    #     return _empty_result_frame()
    # return df

    return _empty_result_frame()


def DATA_LOAD_NODE(state: AgentState) -> Dict[str, Any]:
    """추출된 tasks로 DB 조회 후 결과를 state.db_results에 담는다."""

    tasks = (state.get("extracted_entities") or {}).get("tasks") or []
    df = query_ermap_dataframe(tasks)

    if df.empty:
        return {
            "db_results": [],
            "phase": "no_results",
            "message": "조회 결과가 없습니다.",
        }

    db_results = df.to_dict(orient="records")
    return {
        "db_results": db_results,
        "phase": "queried",
        "message": f"DB 조회 완료 ({len(db_results)}건)",
    }

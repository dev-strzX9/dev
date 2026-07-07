"""ER MAP workflow: extract, repair identifiers, data load, and HITL selection."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Literal, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from ermap_agent import (
    AgentState,
    extract_entities_node,
    repair_task_identifiers_node,
)
from ermap_data_load import DATA_LOAD_NODE
from ermap_selection import (
    ErmapQueryRow,
    format_query_results_message,
    resolve_user_selection,
    rows_from_dicts,
    rows_to_dicts,
)
from llm_api import chat_structured

MAX_ITERATIONS = 30

OrchestratorAction = Literal[
    "extract_entities",
    "repair_task_identifiers",
    "data_load",
    "selection",
    "build_artifact",
    "END",
]


def _update(*, message: str, **fields: Any) -> Dict[str, Any]:
    return {"message": message, **fields}


def resolve_thread_id(emp_no: str, thread_id: Optional[str] = None) -> str:
    """thread_id 없으면 `{emp_no}:{uuid}` 생성. 있으면 그대로 사용."""
    emp_no = emp_no.strip()
    if not emp_no:
        raise ValueError("emp_no is required")
    if thread_id and thread_id.strip():
        return thread_id.strip()
    return f"{emp_no}:{uuid.uuid4()}"


def make_config_from_thread_id(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def is_awaiting_resume(graph: Any, config: Dict[str, Any]) -> bool:
    return bool(graph.get_state(config).next)


def _interrupt_payload_from_value(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        if "message" in value or "phase" in value or "count" in value:
            return value
        nested = value.get("value")
        if isinstance(nested, dict):
            return nested
    return None


def _interrupts_from_invoke_result(invoke_result: Optional[Dict[str, Any]]) -> list[Any]:
    if not invoke_result:
        return []
    raw = invoke_result.get("__interrupt__")
    if not raw:
        return []
    return list(raw)


def get_selection_interrupt_payload(
    graph: Any,
    config: Dict[str, Any],
    *,
    invoke_result: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """HITL interrupt payload (목록 message 등). 대기 중이 아니면 None."""
    snapshot = graph.get_state(config)
    interrupts = list(getattr(snapshot, "interrupts", None) or [])
    if not interrupts:
        interrupts = _interrupts_from_invoke_result(invoke_result)
    if not interrupts:
        return None

    first = interrupts[0]
    if hasattr(first, "value"):
        return _interrupt_payload_from_value(first.value)
    return _interrupt_payload_from_value(first)


def selection_node(state: AgentState) -> Dict[str, Any]:
    db_results = state.get("db_results") or []
    if not db_results:
        return _update(message="조회 결과가 없습니다.", filtered_results=[], phase="no_results")

    rows = rows_from_dicts(db_results)
    if len(rows) == 1:
        return _update(
            message="조회 결과 1건 — 자동 선택",
            filtered_results=rows_to_dicts(rows),
            phase="selected",
        )

    user_reply = interrupt(
        {
            "phase": "awaiting_selection",
            "message": format_query_results_message(rows),
            "count": len(rows),
        }
    )

    reply_text = str(user_reply).strip()
    if not reply_text:
        return _update(
            message="선택 입력이 비어 있습니다.",
            filtered_results=[],
            phase="selection_failed",
        )

    selected_rows, selection_message = resolve_user_selection(reply_text, rows)
    if not selected_rows:
        return _update(
            message=selection_message,
            filtered_results=[],
            phase="selection_failed",
        )

    return _update(
        message=selection_message,
        filtered_results=rows_to_dicts(selected_rows),
        phase="selected",
    )


def build_artifact_node(state: AgentState) -> Dict[str, Any]:
    filtered = state.get("filtered_results") or []
    if not filtered:
        return _update(
            message=state.get("message", "선택된 결과가 없습니다."),
            artifact={},
            phase=state.get("phase", "selection_failed"),
        )

    rows = [ErmapQueryRow.model_validate(item) for item in filtered]
    return _update(
        message=f"ER MAP artifact 생성 완료 ({len(rows)}건)",
        artifact={
            "kind": "ermap_render",
            "rows": [row.model_dump(exclude_none=True) for row in rows],
            "source_query": state.get("user_query"),
        },
        phase="completed",
    )


class OrchestrationOutput(BaseModel):
    next_action: OrchestratorAction
    feedback: str = Field(description="하위 노드에 전달할 짧은 지시")
    orchestrator_reason: str = Field(description="왜 이 행동을 선택했는지 한 문장으로 설명")


def _orchestrator_result(
    *,
    next_action: OrchestratorAction,
    feedback: str,
    orchestrator_reason: str,
    iteration: int,
) -> Dict[str, Any]:
    return {
        "next_action": next_action,
        "feedback": feedback,
        "orchestrator_reason": orchestrator_reason,
        "iteration": iteration,
    }


def _build_progress(state: AgentState) -> str:
    """orchestrator가 LLM에 넘길 진행 상황 텍스트."""
    progress = ""
    tasks = (state.get("extracted_entities") or {}).get("tasks") or []

    # [수정] extracted_entities는 dict({"tasks": [...]})라 len(dict)가 아니라 tasks 길이를 써야 함
    if tasks:
        progress += f"엔티티 추출 완료: {len(tasks)}건\n"
    else:
        progress += "엔티티 추출 필요\n"

    db_results = state.get("db_results") or []
    if db_results:
        # [수정] 1건/다건 문장이 중복되던 부분을 한 줄로 정리
        if len(db_results) == 1:
            progress += "조회 결과 1건 — 자동 선택 가능\n"
        else:
            progress += f"조회 결과 {len(db_results)}건 — HITL 선택 필요\n"
    else:
        progress += "조회 결과 필요\n"

    filtered = state.get("filtered_results") or []
    if filtered:
        progress += f"필터링 결과 완료: {len(filtered)}건\n"
    else:
        progress += "필터링 결과 필요\n"

    artifact = state.get("artifact") or {}
    artifact_rows = artifact.get("rows") or []
    if artifact_rows:
        # [수정] artifact도 dict라 len(artifact) 대신 rows 개수 사용
        progress += f"아티팩트 생성 완료: {len(artifact_rows)}건\n"
    else:
        progress += "아티팩트 생성 필요\n"

    progress += f"현재 phase: {state.get('phase', 'started')}\n"
    progress += f"현재 iteration: {state.get('iteration', 0)}/{MAX_ITERATIONS}"
    return progress


def _rule_based_orchestration(state: AgentState, iteration: int) -> Dict[str, Any]:
    """LLM 오판/파싱 실패 시 사용하는 방어 로직 (노트북 패턴)."""
    tasks = (state.get("extracted_entities") or {}).get("tasks") or []
    db_results = state.get("db_results") or []
    filtered = state.get("filtered_results") or []
    artifact_rows = (state.get("artifact") or {}).get("rows") or []
    phase = state.get("phase", "started")

    if iteration >= MAX_ITERATIONS:
        return _orchestrator_result(
            next_action="END",
            feedback="최대 반복 횟수에 도달했습니다.",
            orchestrator_reason="MAX_ITERATIONS 도달",
            iteration=iteration,
        )

    if not tasks:
        return _orchestrator_result(
            next_action="extract_entities",
            feedback="user_query에서 ER MAP 엔티티를 추출하라.",
            orchestrator_reason="extracted_entities.tasks 가 비어 있음",
            iteration=iteration,
        )

    if phase == "extracted":
        return _orchestrator_result(
            next_action="repair_task_identifiers",
            feedback="추출된 tasks 식별자를 보정하라.",
            orchestrator_reason="1차 추출 완료 후 2차 보정 필요",
            iteration=iteration,
        )

    if phase in {"validated", "extraction_failed"} and not db_results:
        return _orchestrator_result(
            next_action="data_load",
            feedback="보정된 tasks로 DB를 조회하라.",
            orchestrator_reason="식별자 보정 후 DB 조회 필요",
            iteration=iteration,
        )

    if phase == "no_results" or not db_results:
        return _orchestrator_result(
            next_action="END",
            feedback="조회 결과가 없어 워크플로우를 종료한다.",
            orchestrator_reason="db_results 없음",
            iteration=iteration,
        )

    if not filtered:
        return _orchestrator_result(
            next_action="selection",
            feedback="조회 결과에서 사용자 선택 또는 자동 선택을 수행하라.",
            orchestrator_reason="db_results는 있으나 filtered_results 없음",
            iteration=iteration,
        )

    if not artifact_rows:
        return _orchestrator_result(
            next_action="build_artifact",
            feedback="선택된 결과로 ER MAP artifact를 생성하라.",
            orchestrator_reason="filtered_results는 있으나 artifact 없음",
            iteration=iteration,
        )

    return _orchestrator_result(
        next_action="END",
        feedback="모든 단계가 완료되었습니다.",
        orchestrator_reason="artifact 생성 완료",
        iteration=iteration,
    )


def _validate_llm_action(state: AgentState, next_action: str) -> OrchestratorAction:
    """state와 모순되는 LLM 선택을 rule-based 결과로 덮어씀."""
    fallback = _rule_based_orchestration(state, state.get("iteration", 0))
    allowed = {
        "extract_entities",
        "repair_task_identifiers",
        "data_load",
        "selection",
        "build_artifact",
        "END",
    }
    if next_action not in allowed:
        return fallback["next_action"]

    tasks = (state.get("extracted_entities") or {}).get("tasks") or []
    db_results = state.get("db_results") or []
    filtered = state.get("filtered_results") or []

    if not tasks and next_action != "extract_entities":
        return fallback["next_action"]
    if tasks and not db_results and next_action not in {"repair_task_identifiers", "data_load"}:
        return fallback["next_action"]
    if not db_results and next_action in {"selection", "build_artifact"}:
        return fallback["next_action"]
    if not filtered and next_action == "build_artifact":
        return fallback["next_action"]

    return next_action  # type: ignore[return-value]


def orchestrator_node(state: AgentState) -> Dict[str, Any]:
    # [수정] state를 직접 mutate하지 않고 return dict로 iteration을 관리
    iteration = state.get("iteration", 0) + 1

    # [수정] LLM 호출 전에 MAX_ITERATIONS 확인 (기존에는 LLM 호출 후에 체크함)
    if iteration >= MAX_ITERATIONS:
        return _rule_based_orchestration(state, iteration)

    progress = _build_progress({**state, "iteration": iteration})

    try:
        response = chat_structured(
            system_prompt=f"""
You are the orchestrator for an ER MAP workflow.
Choose exactly one next_action based on the current progress.

User query:
{state.get("user_query")}

Progress:
{progress}

Allowed actions:
- extract_entities: when extracted_entities.tasks is empty
- repair_task_identifiers: when phase is extracted
- data_load: when phase is validated and db_results is empty
- selection: when db_results exists and filtered_results is empty
- build_artifact: when filtered_results exists and artifact is empty
- END: when workflow is complete or no_results

Return JSON matching OrchestrationOutput.
""",
            user_content=state.get("user_query", ""),
            response_model=OrchestrationOutput,
        )
        next_action = _validate_llm_action(state, response.next_action)
        return _orchestrator_result(
            next_action=next_action,
            feedback=response.feedback,
            orchestrator_reason=response.orchestrator_reason,
            iteration=iteration,
        )
    except Exception:
        # LLM 실패 시에도 그래프가 진행되도록 rule-based fallback
        return _rule_based_orchestration(state, iteration)


def orch_router(state: AgentState) -> str:
    # [수정] 기존: orchestrator_node()를 다시 호출하고 dict를 반환 → LLM 2번 호출 + 분기 실패
    # [수정] conditional_edges 라우터는 state에 이미 기록된 next_action 문자열만 반환해야 함
    return state.get("next_action") or "END"


checkpointer = MemorySaver()

workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("repair_task_identifiers", repair_task_identifiers_node)
workflow.add_node("data_load", DATA_LOAD_NODE)
workflow.add_node("selection", selection_node)
workflow.add_node("build_artifact", build_artifact_node)
workflow.add_node("orchestrator", orchestrator_node)

# [수정] 기존 next_action 노드는 edge가 없어 dead node였으므로 제거

workflow.add_edge(START, "orchestrator")

# [수정] 기존: "orchestrator_node" (오타) → 실제 노드명 "orchestrator"에 연결
workflow.add_conditional_edges(
    "orchestrator",
    orch_router,
    {
        "extract_entities": "extract_entities",
        "repair_task_identifiers": "repair_task_identifiers",
        "data_load": "data_load",
        "selection": "selection",
        "build_artifact": "build_artifact",
        "END": END,
    },
)

# [수정] 기존: 워커 실행 후 orchestrator로 돌아오는 edge가 없어서 한 번도 루프가 안 돌았음
workflow.add_edge("extract_entities", "orchestrator")
workflow.add_edge("repair_task_identifiers", "orchestrator")
workflow.add_edge("data_load", "orchestrator")
workflow.add_edge("selection", "orchestrator")

# [수정] 완료 노드는 orchestrator 루프 대신 END로 종료
workflow.add_edge("build_artifact", END)

graph = workflow.compile(checkpointer=checkpointer)


def invoke_extraction(
    user_query: str,
    *,
    reference_date: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: AgentState = {"user_query": user_query, "phase": "started"}
    if reference_date:
        payload["reference_date"] = reference_date
    return graph.invoke(payload, config or {})


def invoke_resume(graph_ref: Any, *, user_reply: str, config: Dict[str, Any]) -> Dict[str, Any]:
    return graph_ref.invoke(Command(resume=user_reply), config)

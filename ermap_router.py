"""ER MAP FastAPI router — graph는 여기서 import해서 사용."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ermap_workflow import (
    get_selection_interrupt_payload,
    graph,
    invoke_resume,
    is_awaiting_resume,
    make_config_from_thread_id,
    resolve_thread_id,
)

router = APIRouter(prefix="/ermap", tags=["ermap"])


class ErmapChatRequest(BaseModel):
    emp_no: str = Field(description="사번 (프론트에서 전달)")
    message: str = Field(description="사용자 질문 또는 HITL 선택 답변")
    thread_id: Optional[str] = Field(
        default=None,
        description="없으면 서버에서 uuid 생성. 형식: `{emp_no}:{uuid}`",
    )
    reference_date: Optional[str] = Field(
        default=None,
        description="기준일 YYYY-MM-DD (새 질문일 때만)",
    )


class ErmapChatResponse(BaseModel):
    thread_id: str = Field(description="프론트 저장용. 이후 요청에 그대로 전달")
    status: Literal["awaiting_selection", "done"]
    message: str
    phase: Optional[str] = None
    selection: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


@router.post("/chat", response_model=ErmapChatResponse)
def chat(body: ErmapChatRequest) -> ErmapChatResponse:
    emp_no = body.emp_no.strip()
    message = body.message.strip()
    if not emp_no or not message:
        raise HTTPException(status_code=400, detail="emp_no, message가 필요합니다.")

    try:
        thread_id = resolve_thread_id(emp_no, body.thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = make_config_from_thread_id(thread_id)

    if is_awaiting_resume(graph, config):
        result = invoke_resume(graph, user_reply=message, config=config)
    else:
        payload: Dict[str, Any] = {"user_query": message, "phase": "started"}
        if body.reference_date:
            payload["reference_date"] = body.reference_date
        result = graph.invoke(payload, config)

    if is_awaiting_resume(graph, config):
        selection = get_selection_interrupt_payload(graph, config) or {}
        return ErmapChatResponse(
            thread_id=thread_id,
            status="awaiting_selection",
            message=selection.get("message", "조회 결과에서 선택해 주세요."),
            phase=selection.get("phase"),
            selection=selection,
        )

    return ErmapChatResponse(
        thread_id=thread_id,
        status="done",
        message=result.get("message", ""),
        phase=result.get("phase"),
        result=result,
    )

"""FastAPI backend: ER MAP graph invoke + HITL + thread_id (emp_no:uuid)."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ermap_workflow import (
    create_thread_id,
    get_selection_interrupt_payload,
    graph,
    invoke_resume,
    is_awaiting_resume,
    make_config_from_thread_id,
    thread_id_matches_emp_no,
)

app = FastAPI(title="ER MAP Agent API")


class SessionCreateRequest(BaseModel):
    emp_no: str = Field(description="사번")


class SessionCreateResponse(BaseModel):
    thread_id: str = Field(description="LangGraph thread_id (`사번:uuid`)")
    session_uuid: str = Field(description="대화 session UUID (thread_id 접미사)")


class ChatRequest(BaseModel):
    emp_no: str = Field(description="사번")
    thread_id: str = Field(description="`/ermap/session`에서 받은 thread_id")
    message: str = Field(description="사용자 질문 또는 HITL 선택 답변")
    reference_date: Optional[str] = Field(
        default=None,
        description="기준일 YYYY-MM-DD (새 질문일 때만 사용)",
    )


class ChatResponse(BaseModel):
    thread_id: str
    status: Literal["awaiting_selection", "done"]
    message: str
    phase: Optional[str] = None
    selection: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None


def _validate_thread(emp_no: str, thread_id: str) -> None:
    if not thread_id_matches_emp_no(thread_id, emp_no):
        raise HTTPException(
            status_code=400,
            detail="thread_id가 emp_no와 일치하지 않습니다.",
        )


@app.post("/ermap/session", response_model=SessionCreateResponse)
def create_session(body: SessionCreateRequest) -> SessionCreateResponse:
    """새 대화 session 생성. 프론트는 thread_id를 저장해 이후 chat에 넘깁니다."""
    emp_no = body.emp_no.strip()
    if not emp_no:
        raise HTTPException(status_code=400, detail="emp_no가 비어 있습니다.")

    thread_id, session_uuid = create_thread_id(emp_no)
    return SessionCreateResponse(thread_id=thread_id, session_uuid=session_uuid)


@app.post("/ermap/chat", response_model=ChatResponse)
def chat(body: ChatRequest) -> ChatResponse:
    """질문 invoke 또는 HITL resume. 같은 thread_id로 요청을 이어갑니다."""
    emp_no = body.emp_no.strip()
    thread_id = body.thread_id.strip()
    message = body.message.strip()

    if not emp_no or not thread_id or not message:
        raise HTTPException(status_code=400, detail="emp_no, thread_id, message가 필요합니다.")
    _validate_thread(emp_no, thread_id)

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
        return ChatResponse(
            thread_id=thread_id,
            status="awaiting_selection",
            message=selection.get("message", "조회 결과에서 선택해 주세요."),
            phase=selection.get("phase"),
            selection=selection,
        )

    return ChatResponse(
        thread_id=thread_id,
        status="done",
        message=result.get("message", ""),
        phase=result.get("phase"),
        result=result,
    )

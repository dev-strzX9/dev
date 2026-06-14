# ER MAP Entity Extraction

반도체 ER MAP 조회용 **1차/2차 LLM 엔티티 추출** 모듈입니다.

## 포함 범위

```
user_query → 1차 LLM (extract_entities) → 2차 LLM (repair identifiers) → tasks
```

DB 조회, HITL 선택, 렌더링은 **포함하지 않습니다.**

## 파일

| 파일 | 역할 |
|------|------|
| `ermap_agent.py` | 1차/2차 추출 노드, Pydantic 모델 |
| `llm_api.py` | OpenRouter `requests` API |
| `ermap_prompt.yaml` | 1차 추출 프롬프트 |
| `ermap_repair_prompt.yaml` | 2차 식별자 보정 프롬프트 |
| `ermap_workflow.py` | LangGraph 워크플로우, `graph`, `invoke_extraction()` |

## 설정

```bash
export OPENROUTER_API_KEY='sk-or-v1-...'
export OPENROUTER_MODEL='nvidia/nemotron-3-ultra-550b-a55b:free'
pip install -r requirements.txt
```

## 사용

```python
from ermap_workflow import graph, invoke_extraction

# 권장: invoke 헬퍼
result = invoke_extraction(
    "최근 일주일 EFG4803 PM1 BEVEL BACKSIDE MAP 보여줘",
    reference_date="2026-05-29",
)

# 또는 graph 직접 호출
result = graph.invoke({
    "user_query": "최근 일주일 EFG4803 PM1 BEVEL 보여줘",
    "reference_date": "2026-05-29",
    "phase": "started",
})

print(result["phase"])                 # validated | extraction_failed
print(result["extracted_entities"])    # {"tasks": [...]}
```

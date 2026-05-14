# Milvus Query Rewriter

Milvus 벡터 DB 에서 유사 문서를 검색하고, 그 결과를 LLM 에 컨텍스트로 넣어
**원본 내용 요약 / 재작성 쿼리 / 원본 쿼리** 3가지를 반환하는 간단한 예시 코드.

## 흐름

```
User Query
   └─ embed
        └─ Milvus client.search (top_k)
              └─ LLM 호출 (요약 + 쿼리 재작성)
                    └─ {summary, rewritten_query, original_query}
```

## 설치

```bash
pip install -r requirements.txt
```

## 환경 변수

| 변수 | 설명 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API key |
| `MILVUS_URI` | Milvus 엔드포인트 (예: `http://localhost:19530`) |
| `MILVUS_TOKEN` | Milvus 인증 토큰(필요 시) |
| `MILVUS_COLLECTION` | 검색 대상 컬렉션 이름 |

## 사용

```python
from query_rewriter import MilvusQueryRewriter

rewriter = MilvusQueryRewriter(
    milvus_uri="http://localhost:19530",
    collection_name="documents",
    text_field="text",
    vector_field="embedding",
    top_k=5,
)

result = rewriter.run("LLM 기반 검색 시스템에서 쿼리 재작성은 어떻게 하나요?")
print(result.summary)          # 검색된 문서 요약
print(result.rewritten_query)  # 재작성된 쿼리
print(result.original_query)   # 원본 쿼리
```

## 컬렉션 가정

기본값은 다음과 같은 스키마를 가정합니다. 다르면 생성자 인자로 바꿔 주세요.

- `embedding` : `FLOAT_VECTOR` (검색용 벡터 필드)
- `text` : `VARCHAR` (원본 텍스트 필드)

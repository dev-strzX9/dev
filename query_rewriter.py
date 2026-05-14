"""
Milvus 검색 결과 기반 LLM Query Rewriter

흐름:
    1) 사용자 Query 입력
    2) Query 를 임베딩 후 Milvus 에서 유사 문서 검색 (client.search)
    3) 검색된 문서를 컨텍스트로 LLM 에 전달하여
       - 원본 문서 요약 (summary)
       - 재작성 쿼리 (rewritten_query)
       를 JSON 으로 받음
    4) (summary, rewritten_query, original_query) 3-튜플 형태로 반환
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import List

from pymilvus import MilvusClient
from openai import OpenAI


# --------------------------------------------------------------------------- #
# 1. 결과 데이터 클래스
# --------------------------------------------------------------------------- #
@dataclass
class RewriteResult:
    summary: str           # 검색된 원본 내용 요약
    rewritten_query: str   # LLM 이 다시 쓴 쿼리
    original_query: str    # 사용자가 처음 입력한 쿼리

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 2. Rewriter 본체
# --------------------------------------------------------------------------- #
class MilvusQueryRewriter:
    def __init__(
        self,
        milvus_uri: str = "http://localhost:19530",
        milvus_token: str | None = None,
        collection_name: str = "documents",
        text_field: str = "text",
        vector_field: str = "embedding",
        embed_model: str = "text-embedding-3-small",
        llm_model: str = "gpt-4o-mini",
        top_k: int = 5,
        openai_api_key: str | None = None,
    ) -> None:
        self.collection_name = collection_name
        self.text_field = text_field
        self.vector_field = vector_field
        self.embed_model = embed_model
        self.llm_model = llm_model
        self.top_k = top_k

        self.milvus = MilvusClient(uri=milvus_uri, token=milvus_token)
        self.openai = OpenAI(api_key=openai_api_key or os.getenv("OPENAI_API_KEY"))

    # ---------- 내부 유틸 ---------- #
    def _embed(self, text: str) -> List[float]:
        resp = self.openai.embeddings.create(model=self.embed_model, input=text)
        return resp.data[0].embedding

    def _search(self, query: str) -> List[str]:
        """Milvus 에서 top_k 개 문서의 text 필드를 가져온다."""
        query_vec = self._embed(query)
        results = self.milvus.search(
            collection_name=self.collection_name,
            data=[query_vec],
            anns_field=self.vector_field,
            limit=self.top_k,
            output_fields=[self.text_field],
        )

        docs: List[str] = []
        for hit in results[0]:
            entity = hit.get("entity", {}) if isinstance(hit, dict) else {}
            text = entity.get(self.text_field) or hit.get(self.text_field, "")
            if text:
                docs.append(str(text))
        return docs

    def _llm_rewrite(self, query: str, docs: List[str]) -> tuple[str, str]:
        """LLM 호출 → (summary, rewritten_query)."""
        context = "\n\n".join(f"[Doc {i+1}] {d}" for i, d in enumerate(docs))

        system_prompt = (
            "당신은 검색 품질 개선을 위한 Query Rewriter 입니다. "
            "주어진 사용자 쿼리와 관련 문서들을 보고 두 가지를 생성하세요.\n"
            "1) summary: 검색된 문서들의 핵심 내용을 2~3문장으로 간단히 요약\n"
            "2) rewritten_query: 검색 정확도를 높일 수 있도록 구체화·명확화된 쿼리\n"
            '반드시 다음 JSON 형식으로만 응답: {"summary": "...", "rewritten_query": "..."}'
        )

        user_prompt = (
            f"[원본 쿼리]\n{query}\n\n"
            f"[검색된 문서]\n{context if context else '(검색 결과 없음)'}"
        )

        resp = self.openai.chat.completions.create(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )

        data = json.loads(resp.choices[0].message.content)
        return data.get("summary", "").strip(), data.get("rewritten_query", "").strip()

    # ---------- 외부 API ---------- #
    def run(self, query: str) -> RewriteResult:
        docs = self._search(query)
        summary, rewritten = self._llm_rewrite(query, docs)
        return RewriteResult(
            summary=summary,
            rewritten_query=rewritten,
            original_query=query,
        )


# --------------------------------------------------------------------------- #
# 3. 사용 예시
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    rewriter = MilvusQueryRewriter(
        milvus_uri=os.getenv("MILVUS_URI", "http://localhost:19530"),
        milvus_token=os.getenv("MILVUS_TOKEN"),
        collection_name=os.getenv("MILVUS_COLLECTION", "documents"),
    )

    user_query = "LLM 기반 검색 시스템에서 쿼리 재작성은 어떻게 하나요?"
    result = rewriter.run(user_query)

    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))

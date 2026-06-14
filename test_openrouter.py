#!/usr/bin/env python3
"""Test ER MAP entity extraction via OpenRouter."""

from __future__ import annotations

import argparse
import json
import os
import sys

from ermap_agent import (
    extract_entities_node,
    repair_task_identifiers_node,
    run_entity_extraction,
)
from llm_api import chat_completion


def _require_openrouter_key() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY 환경 변수가 필요합니다.", file=sys.stderr)
        sys.exit(1)


def test_ping() -> None:
    content = chat_completion([{"role": "user", "content": "Reply with exactly: pong"}])
    print("=== ping ===")
    print(content)


def test_extract(user_query: str, reference_date: str) -> None:
    result = run_entity_extraction(user_query, reference_date=reference_date)
    print("=== run_entity_extraction ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def test_stage1(user_query: str, reference_date: str) -> None:
    state = extract_entities_node(
        {"user_query": user_query, "reference_date": reference_date}
    )
    print("=== stage-1 extract_entities ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))


def test_stage2(user_query: str, reference_date: str) -> None:
    extracted = extract_entities_node(
        {"user_query": user_query, "reference_date": reference_date}
    )
    repaired = repair_task_identifiers_node(
        {
            "user_query": user_query,
            "extracted_entities": extracted["extracted_entities"],
        }
    )
    print("=== stage-2 repair_task_identifiers ===")
    print(json.dumps(repaired, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Test ER MAP entity extraction")
    parser.add_argument(
        "--mode",
        choices=("ping", "extract", "stage1", "stage2"),
        default="extract",
    )
    parser.add_argument(
        "--query",
        default="최근 일주일 EFG4803 PM1 BEVEL BACKSIDE MAP 보여줘",
    )
    parser.add_argument("--reference-date", default="2026-05-29")
    args = parser.parse_args()

    _require_openrouter_key()

    if args.mode == "ping":
        test_ping()
    elif args.mode == "extract":
        test_extract(args.query, args.reference_date)
    elif args.mode == "stage1":
        test_stage1(args.query, args.reference_date)
    elif args.mode == "stage2":
        test_stage2(args.query, args.reference_date)


if __name__ == "__main__":
    main()

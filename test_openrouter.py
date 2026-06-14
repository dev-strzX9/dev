#!/usr/bin/env python3
"""Smoke-test ER MAP agent LLM calls via OpenRouter."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from ermap_agent import ErmapEntities, extract_entities_node, repair_task_identifiers_node
from ermap_llm import create_chat_llm, get_llm_config_summary


def _require_openrouter_key() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        print(
            "OPENROUTER_API_KEY 환경 변수가 필요합니다.\n"
            "예: export OPENROUTER_API_KEY='sk-or-v1-...'\n"
            "예: export ERMAP_LLM_MODEL='nvidia/nemotron-3-ultra-550b-a55b:free'",
            file=sys.stderr,
        )
        sys.exit(1)


def test_ping() -> None:
    llm = create_chat_llm()
    response = llm.invoke([HumanMessage(content="Reply with exactly: pong")])
    print("=== ping ===")
    print(get_llm_config_summary())
    print(response.content)


def test_stage1_extract(user_query: str, reference_date: str) -> None:
    state = extract_entities_node(
        {
            "user_query": user_query,
            "reference_date": reference_date,
        }
    )
    print("=== stage-1 extract_entities ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))


def test_stage2_repair(user_query: str, reference_date: str) -> None:
    extracted = extract_entities_node(
        {
            "user_query": user_query,
            "reference_date": reference_date,
        }
    )
    repaired = repair_task_identifiers_node(
        {
            "user_query": user_query,
            "extracted_entities": extracted["extracted_entities"],
        }
    )
    print("=== stage-2 repair_task_identifiers ===")
    print(json.dumps(repaired, ensure_ascii=False, indent=2))


def test_structured_extract(user_query: str, reference_date: str) -> None:
    from pathlib import Path

    import yaml

    prompt_path = Path(__file__).with_name("ermap_prompt.yaml")
    prompt_config = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    system_prompt = prompt_config["system_prompt"].replace(
        "{reference_date}",
        reference_date,
    )

    llm = create_chat_llm()
    parsed = llm.with_structured_output(
        ErmapEntities,
        method="function_calling",
    ).invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_query),
        ]
    )

    print("=== structured stage-1 ===")
    if hasattr(parsed, "model_dump"):
        print(json.dumps(parsed.model_dump(exclude_none=True), ensure_ascii=False, indent=2))
    else:
        print(parsed)


def test_full_graph(user_query: str, reference_date: str) -> None:
    from ermap_agent import graph, invoke_new_query, make_config

    thread_id = f"test-{uuid.uuid4().hex[:8]}"
    config = make_config("openrouter-test", thread_id)
    result = invoke_new_query(
        graph,
        user_query=user_query,
        reference_date=reference_date,
        config=config,
    )
    print("=== full graph (until selection interrupt or end) ===")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Test ER MAP agent with OpenRouter")
    parser.add_argument(
        "--mode",
        choices=("ping", "structured", "stage1", "stage2", "full"),
        default="full",
        help="Which test to run",
    )
    parser.add_argument(
        "--query",
        default="최근 일주일 EFG4803 PM1 BEVEL BACKSIDE MAP 보여줘",
        help="User query for agent tests",
    )
    parser.add_argument(
        "--reference-date",
        default="2026-05-29",
        help="Reference date for relative date parsing",
    )
    args = parser.parse_args()

    _require_openrouter_key()
    print(get_llm_config_summary())
    print()

    if args.mode == "ping":
        test_ping()
    elif args.mode == "structured":
        test_structured_extract(args.query, args.reference_date)
    elif args.mode == "stage1":
        test_stage1_extract(args.query, args.reference_date)
    elif args.mode == "stage2":
        test_stage2_repair(args.query, args.reference_date)
    elif args.mode == "full":
        test_full_graph(args.query, args.reference_date)


if __name__ == "__main__":
    main()

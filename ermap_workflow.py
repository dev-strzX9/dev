"""ER MAP entity extraction workflow (stage-1 + stage-2)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from ermap_agent import (
    AgentState,
    extract_entities_node,
    repair_task_identifiers_node,
)

workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("repair_task_identifiers", repair_task_identifiers_node)

workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", "repair_task_identifiers")
workflow.add_edge("repair_task_identifiers", END)

graph = workflow.compile()


def invoke_extraction(
    user_query: str,
    *,
    reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the extraction workflow with graph.invoke()."""

    payload: AgentState = {"user_query": user_query, "phase": "started"}
    if reference_date:
        payload["reference_date"] = reference_date
    return graph.invoke(payload)

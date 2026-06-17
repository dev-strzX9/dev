"""ER MAP workflow: extract, repair identifiers, and data load."""

from __future__ import annotations

from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph

from ermap_agent import (
    AgentState,
    extract_entities_node,
    repair_task_identifiers_node,
)
from ermap_data_load import DATA_LOAD_NODE


workflow = StateGraph(AgentState)
workflow.add_node("extract_entities", extract_entities_node)
workflow.add_node("repair_task_identifiers", repair_task_identifiers_node)
workflow.add_node("data_load", DATA_LOAD_NODE)

workflow.add_edge(START, "extract_entities")
workflow.add_edge("extract_entities", "repair_task_identifiers")
workflow.add_edge("repair_task_identifiers", "data_load")
workflow.add_edge("data_load", END)

graph = workflow.compile()


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

"""Pure, best-effort search copies. Original JSON is never mutated.

RowList.warnings carries skipped-row diagnostics while preserving the documented
(node_rows, edge_rows) return signature.
"""
import math


class RowList(list):
    def __init__(self):
        super().__init__()
        self.warnings = []


def string(obj, key, default="", required=False):
    value = obj.get(key, default)
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{key} must be {'nonempty ' if required else ''}text")
    return value


def derive_flow_rows(doc):
    nodes, edges = RowList(), RowList()
    node_keys, edge_keys = set(), set()
    for index, block in enumerate(doc.get("blocks", [])):
        if not isinstance(block, dict) or block.get("type") != "flowchart":
            continue
        try:
            instance = string(block, "instanceId", required=True)
            project = block.get("project")
            if not isinstance(project, dict): raise ValueError("project must be an object")
        except ValueError as exc:
            nodes.warnings.append(f"blocks[{index}]: {exc}")
            continue
        for kind, target, seen in (("nodes", nodes, node_keys), ("edges", edges, edge_keys)):
            items = project.get(kind, [])
            if not isinstance(items, list):
                target.warnings.append(f"{instance}.{kind}: expected an array")
                continue
            for row_index, item in enumerate(items):
                try:
                    if not isinstance(item, dict): raise ValueError("expected an object")
                    key = string(item, "node" if kind == "nodes" else "edge", required=True)
                    if (instance, key) in seen: raise ValueError(f"duplicate key {key}")
                    row = {"instance_id": instance}
                    if kind == "nodes":
                        node_type = string(item, "node_type", required=True)
                        if node_type not in {"start", "seq", "decision", "sop", "end"}: raise ValueError("invalid node_type")
                        row.update(node_key=key, node_type=node_type)
                        for field in ("name", "role_owner", "action", "description"):
                            row[field] = string(item, field)
                        for field in ("systems", "manual"):
                            value = item.get(field, [])
                            if not isinstance(value, list): raise ValueError(f"{field} must be an array")
                            row[field] = value
                        if any(not isinstance(s, dict) or not isinstance(s.get("name", ""), str) or not isinstance(s.get("menus", []), list) or any(not isinstance(m, str) for m in s.get("menus", [])) for s in row["systems"]):
                            raise ValueError("invalid systems entry")
                        row["ref_sop_no"] = string(item, "sop_id")
                        row["ref_sop_name"] = string(item, "sop_name")
                        row["position"] = {}
                        for field in ("x", "y"):
                            if field in item:
                                v = item[field]
                                if type(v) not in (int, float) or not math.isfinite(v): raise ValueError(f"invalid {field}")
                                row["position"][field] = v
                        size = item.get("font_size")
                        if size is not None and (type(size) is not int or not -(2**31) <= size < 2**31): raise ValueError("invalid font_size")
                        row["font_size"] = size
                    else:
                        row.update(edge_key=key, source_key=string(item, "source", required=True), target_key=string(item, "target", required=True))
                        for src, dst in (("sourcePort", "source_port"), ("targetPort", "target_port"), ("condition", "condition")):
                            row[dst] = string(item, src)
                        row["line_type"] = string(item, "line_type", "orthogonal")
                        row["route"] = item.get("route", {})
                        if not isinstance(row["route"], (dict, list)): raise ValueError("invalid route")
                    target.append(row)
                    seen.add((instance, key))
                except (ValueError, TypeError, OverflowError) as exc:
                    target.warnings.append(f"{instance}.{kind}[{row_index}]: {exc}")
    return nodes, edges

from copy import deepcopy
import pytest
from app.derive import derive_flow_rows


def test_rows_and_original_unchanged(doc):
    original=deepcopy(doc)
    nodes,edges=derive_flow_rows(doc)
    assert len(nodes)==3 and len(edges)==1
    assert nodes[1]['systems']==[{"name":"MES","menus":["Lot"]}]
    assert nodes[0]['position']=={'x':10,'y':20}
    assert edges[0]['source_port']=='bottom'
    assert doc==original
    assert not nodes.warnings and not edges.warnings


@pytest.mark.parametrize('bad',[None,3,{}, {'node':'x','node_type':'unknown'}, {'node':'x','node_type':'seq','font_size':'13'}, {'node':'x','node_type':'seq','systems':{}}, {'node':'x','node_type':'seq','action':[]}])
def test_bad_node_is_skipped(doc,bad):
    doc['blocks'][0]['project']['nodes'].append(bad)
    nodes,edges=derive_flow_rows(doc)
    assert len(nodes)==3 and len(edges)==1 and nodes.warnings


def test_duplicate_nodes_edges_and_instances(doc):
    doc['blocks'].append(deepcopy(doc['blocks'][0]))
    nodes,edges=derive_flow_rows(doc)
    assert len(nodes)==3 and len(edges)==1
    assert len(nodes.warnings)==3 and len(edges.warnings)==1


def test_missing_project_and_bad_edges_are_tolerated(doc):
    doc['blocks'] += [{'type':'flowchart','instanceId':'bad','project':None},None]
    doc['blocks'][0]['project']['edges'] += [{'edge':'broken','source':None}]
    nodes,edges=derive_flow_rows(doc)
    assert len(nodes)==3 and len(edges)==1
    assert nodes.warnings and edges.warnings

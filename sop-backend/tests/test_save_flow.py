from copy import deepcopy
from uuid import uuid4
import pytest
from conftest import save


async def test_save_version_roundtrip_and_search_copies(client,doc):
    r=await save(client,doc)
    assert r.status_code==201, r.text
    first=r.json(); assert first['version_no']==1
    doc['blocks'][0]['project']['nodes'][1]['action']='변경'
    second=(await save(client,doc,1)).json()
    assert second['version_no']==2 and first['id']==second['id']
    opened=await client.get('/api/sops/'+first['id'])
    assert opened.json()['content']==doc
    old=await client.get(f"/api/sops/{first['id']}/versions/1")
    assert old.json()['content']['blocks'][0]['project']['nodes'][1]['action']=='점검'
    assert old.json()['version_id']==first['version_id']
    by_no=await client.get('/api/sops/by-no/'+doc['sop']['id'])
    assert by_no.json()==opened.json()
    async with client.pool.connection() as conn:
        counts=await (await conn.execute('SELECT count(*) AS n FROM flow_nodes')).fetchone()
        assert counts['n']==6
        counts=await (await conn.execute('SELECT count(*) AS n FROM flow_edges')).fetchone()
        assert counts['n']==2
    rows=(await client.get('/api/sops?q=Chamber&area=P')).json()
    assert len(rows)==1 and rows[0]['version_no']==2 and 'content' not in rows[0]
    assert (await client.get('/api/sops?q=NO-MATCH')).json()==[]
    assert (await client.get('/api/sops?q=%25')).json()==[]
    versions=(await client.get(f"/api/sops/{first['id']}/versions")).json()
    assert [v['version_no'] for v in versions]==[2,1] and 'content' not in versions[0]
    diff=(await client.get(f"/api/sops/{first['id']}/versions/1/diff/2")).json()
    assert len(diff['modified'])==1 and not diff['added'] and not diff['deleted']


async def test_derive_errors_do_not_lose_document(client,doc):
    doc['studio']['area']='invalid'
    doc['blocks'][0]['project']['nodes'].append({'node':'bad','node_type':'invalid'})
    r=await save(client,doc)
    assert r.status_code==201 and len(r.json()['warnings'])==2
    result=(await client.get('/api/sops/'+r.json()['id'])).json()
    assert result['content']==doc
    assert (await client.get('/api/sops')).json()[0]['area']==''


async def test_retire_restore_history_preserved(client,doc):
    item=(await save(client,doc)).json(); path='/api/sops/'+item['id']
    assert (await client.delete(path)).json()['status']=='retired'
    assert (await client.get('/api/sops')).json()==[]
    assert len((await client.get('/api/sops?status=retired')).json())==1
    assert (await client.get(path)).json()['content']==doc
    assert (await client.post(path+'/restore')).json()['status']=='draft'
    assert len((await client.get('/api/sops')).json())==1


async def test_not_found_validation_health_static(client,doc):
    assert (await client.get('/api/health')).json()=={'ok':True,'db':'up'}
    assert (await client.get('/')).status_code==200
    assert (await client.get('/static/library.js')).status_code==200
    for path in [f'/api/sops/{uuid4()}', '/api/sops/by-no/missing',f'/api/sops/{uuid4()}/versions']:
        r=await client.get(path);assert r.status_code==404 and r.json()['error']['code']=='not_found'
    r=await client.put('/api/sops/MISMATCH',json={'doc':doc})
    assert r.status_code==400
    r=await client.put('/api/sops/A',json={'doc':{'format':'wrong'}})
    assert r.status_code==422 and 'error' in r.json()
    assert (await client.get('/api/sops?status=bad')).status_code==422


async def test_header_user_fallback(client,doc):
    response=await client.put('/api/sops/'+doc['sop']['id'],json={'doc':doc},headers={'X-User':'header-user'})
    item=response.json()
    versions=(await client.get(f"/api/sops/{item['id']}/versions")).json()
    assert versions[0]['saved_by']=='header-user'


async def test_diff_added_deleted(client,doc):
    item=(await save(client,doc)).json()
    doc['blocks'][0]['project']['nodes'].pop()
    doc['blocks'][0]['project']['nodes'].append({'node':'new','node_type':'end'})
    await save(client,doc,1)
    diff=(await client.get(f"/api/sops/{item['id']}/versions/1/diff/2")).json()
    assert diff['added'][0]['node_key']=='new'
    assert diff['deleted'][0]['node_key']=='end_1'
    assert (await client.get(f"/api/sops/{item['id']}/versions/1/diff/99")).status_code==404

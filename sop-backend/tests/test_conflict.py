import asyncio
from copy import deepcopy
from conftest import save


async def test_stale_save_rolls_back_metadata_and_rows(client,doc):
    item=(await save(client,doc)).json()
    doc['sop']['name']='must not persist'
    response=await save(client,doc,0)
    assert response.status_code==409
    assert response.json()['error']['current_version_no']==1
    current=(await client.get('/api/sops/'+item['id'])).json()
    assert current['version_no']==1 and current['content']['sop']['name']=='Chamber PM'
    assert (await client.get('/api/sops')).json()[0]['name']=='Chamber PM'
    async with client.pool.connection() as conn:
        assert (await (await conn.execute('SELECT count(*) AS n FROM flow_nodes')).fetchone())['n']==3


async def test_simultaneous_new_document_saves(client,doc):
    responses=await asyncio.gather(*(save(client,doc,0) for _ in range(4)))
    assert sorted(r.status_code for r in responses)==[201,409,409,409]


async def test_simultaneous_existing_document_saves(client,doc):
    await save(client,doc)
    responses=await asyncio.gather(*(save(client,doc,1) for _ in range(4)))
    assert sorted(r.status_code for r in responses)==[201,409,409,409]
    assert (await client.get('/api/sops')).json()[0]['version_no']==2


async def test_explicit_null_serializes_force_versions(client,doc):
    responses=await asyncio.gather(*(save(client,doc,None) for _ in range(4)))
    assert all(r.status_code==201 for r in responses)
    assert sorted(r.json()['version_no'] for r in responses)==[1,2,3,4]


async def test_failed_first_save_leaves_no_empty_document(client,doc):
    assert (await save(client,doc,10)).status_code==409
    async with client.pool.connection() as conn:
        assert (await (await conn.execute('SELECT count(*) AS n FROM sop_documents')).fetchone())['n']==0

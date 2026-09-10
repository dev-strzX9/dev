import asyncio
from conftest import save


async def test_lock_conflict_heartbeat_release_expiration(client,doc):
    item=(await save(client,doc)).json();path=f"/api/sops/{item['id']}/lock"
    first=await client.post(path,json={'user':'alice','ttl_sec':120})
    assert first.status_code==200
    denied=await client.post(path,json={'user':'bob'})
    assert denied.status_code==423 and denied.json()['error']['locked_by']=='alice'
    renewed=await client.post(path,json={'user':'alice','ttl_sec':300})
    assert renewed.json()['expires_at']>first.json()['expires_at']
    wrong=await client.request('DELETE',path,json={'user':'bob'})
    assert wrong.json()=={'released':False}
    warning=await save(client,doc,1,user='bob')
    assert warning.status_code==201 and warning.json()['warning']['locked_by']=='alice'
    async with client.pool.connection() as conn:
        await conn.execute("UPDATE sop_edit_locks SET expires_at=clock_timestamp()-interval '1 second'")
    assert (await client.get('/api/sops/'+item['id'])).json()['lock'] is None
    assert (await client.post(path,json={'user':'bob'})).status_code==200
    assert (await client.request('DELETE',path,json={'user':'bob'})).json()['released']
    assert (await client.post(path,json={'user':'bob','ttl_sec':0})).status_code==422


async def test_concurrent_lock_ownership(client,doc):
    item=(await save(client,doc)).json();path=f"/api/sops/{item['id']}/lock"
    results=await asyncio.gather(*(client.post(path,json={'user':str(i)}) for i in range(4)))
    assert sorted(r.status_code for r in results)==[200,423,423,423]

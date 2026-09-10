from conftest import save


async def test_drafts_are_per_user_and_save_clears_only_author(client,doc):
    item=(await save(client,doc)).json();path=f"/api/sops/{item['id']}/draft"
    for user in ['hong','bob']:
        assert (await client.put(path,json={'user':user,'content':doc})).status_code==200
    doc['extra']='draft-only'
    await client.put(path,json={'user':'hong','content':doc})
    assert (await client.get(path+'?user=hong')).json()['content']==doc
    assert (await client.get('/api/sops/'+item['id'])).json()['version_no']==1
    await save(client,doc,1)
    assert (await client.get(path+'?user=hong')).status_code==404
    assert (await client.get(path+'?user=bob')).status_code==200
    assert (await client.delete(path+'?user=bob')).status_code==200
    assert (await client.get(path+'?user=bob')).status_code==404

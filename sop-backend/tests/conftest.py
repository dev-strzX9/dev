import os
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import httpx
import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import make_conninfo, conninfo_to_dict
from app.config import Settings
from app.main import create_app


@pytest.fixture
def doc():
    return {"format":"sop-editor-mock","version":1,"sop":{"id":"SOP-TEST-001","name":"Chamber PM","desc":"purpose"},
            "studio":{"area":"P","owner":"hong","revision":"1.0","tags":["PM"]},"future":{"keep":True},
            "blocks":[{"type":"flowchart","instanceId":"flow_1","project":{"nodes":[
                {"node":"start_1","node_type":"start","name":"시작","x":10,"y":20},
                {"node":"seq_1","node_type":"seq","action":"점검","role_owner":"engineer","systems":[{"name":"MES","menus":["Lot"]}],"manual":[],"font_size":13},
                {"node":"end_1","node_type":"end","name":"종료"}],
                "edges":[{"edge":"edge_1","source":"start_1","target":"seq_1","condition":"Yes","sourcePort":"bottom","targetPort":"top"}]}},
                {"type":"body-template","title":"<h2>Example</h2>","objects":[{"kind":"image","src":"data:image/png;base64,YQ==","alt":"sample"}]}]}


@pytest_asyncio.fixture
async def client():
    url=os.getenv("TEST_DATABASE_URL")
    if not url: pytest.skip("Set TEST_DATABASE_URL to a separate PostgreSQL database for integration tests")
    if conninfo_to_dict(url)==conninfo_to_dict(Settings().database_url):
        pytest.fail("TEST_DATABASE_URL must differ from DATABASE_URL")
    schema="sop_test_"+uuid4().hex
    admin=await psycopg.AsyncConnection.connect(url,autocommit=True)
    await admin.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public")
    await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    test_url=make_conninfo(url, options=f"-c search_path={schema},public")
    try:
        async with await psycopg.AsyncConnection.connect(test_url,autocommit=True) as setup:
            await setup.execute((Path(__file__).parents[1]/"sql/sop_schema.sql").read_text())
        app=create_app(Settings(database_url=test_url))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url="http://test") as value:
                value.pool=app.state.pool
                yield value
    finally:
        await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        await admin.close()


async def save(client,doc,base=0,user="hong",**extra):
    return await client.put('/api/sops/'+doc['sop']['id'],json={"doc":deepcopy(doc),"base_version_no":base,"saved_by":user,**extra})

import json
from copy import deepcopy
import httpx
import pytest
from pydantic import ValidationError
from app.config import MAX_CONTENT_BYTES, MAX_REQUEST_BYTES
from app.errors import APIError
from app.main import create_app
from app.schemas import SaveRequest


@pytest.mark.parametrize('key,value',[('format','bad'),('version',True),('version','1'),('blocks',{}),('sop',{'id':'has space'}),('sop',{'id':'../x'}),('sop',{'id':''})])
def test_reject_invalid_envelope(doc,key,value):
    doc[key]=value
    with pytest.raises(ValidationError): SaveRequest(doc=doc)


def test_content_preserves_unknown_fields(doc):
    doc['studio']['area']='INVALID'
    original=deepcopy(doc)
    assert SaveRequest(doc=doc).doc==original


def test_size_limit(doc):
    doc['large']='a'*MAX_CONTENT_BYTES
    with pytest.raises(APIError) as error: SaveRequest(doc=doc)
    assert error.value.status==413


@pytest.mark.parametrize('value',[float('nan'),float('inf'),'bad\x00text','\ud800'])
def test_jsonb_incompatible_value_rejected(doc,value):
    doc['extra']=value
    with pytest.raises(ValidationError): SaveRequest(doc=doc)


async def test_error_envelope_without_database():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(),raise_app_exceptions=False),base_url='http://test') as client:
        r=await client.get('/api/no-such-route')
        assert r.status_code==404 and r.json()['error']['code']=='not_found'
        assert r.headers['x-content-type-options']=='nosniff'


async def test_chunked_request_limit():
    async def chunks():
        for _ in range(MAX_REQUEST_BYTES//1024+1): yield b'x'*1024
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()),base_url='http://test') as client:
        r=await client.put('/api/sops/A',content=chunks())
        assert r.status_code==413 and r.json()['error']['code']=='content_too_large'

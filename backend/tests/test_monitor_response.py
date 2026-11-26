from datetime import datetime,timezone,date
from decimal import Decimal
import json
import uuid
import pytest
from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse
from backend.app import monitor_response


def test_monitor_response_matches_existing_json_conversion():
    data={'checked_at':datetime.now(timezone.utc),'date':date(2026,11,26),
          'id':uuid.uuid4(),'number':Decimal('1.25'),'cases':[{'name':'诊断','waiting':None,
          'reasons':[{'code':'offline','nodes':[{'enabled':False,'value':1.2}]}]}]}
    expected=JSONResponse(jsonable_encoder(data))
    response=monitor_response.MonitorResponse(data)
    assert response.body==expected.body
    assert response.headers['content-type']=='application/json'
    assert json.loads(response.body)['cases'][0]['waiting'] is None


def test_large_native_diagnostic_tree_does_not_reencode_every_leaf(monkeypatch):
    at=datetime.now(timezone.utc)
    calls=[]
    def encode(value):
        calls.append(value);return jsonable_encoder(value)
    monkeypatch.setattr(monitor_response,'jsonable_encoder',encode)
    data={'checked_at':at,'cases':[{'nodes':[{'name':str(i),'ready':False} for i in range(100)]} for _ in range(100)]}
    response=monitor_response.MonitorResponse(data)
    assert len(json.loads(response.body)['cases'])==100
    assert calls==[at]


@pytest.mark.parametrize('value',[float('nan'),float('inf'),-float('inf')])
def test_non_finite_values_still_rejected(value):
    with pytest.raises(ValueError):monitor_response.MonitorResponse({'value':value})

from datetime import datetime, timezone
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app.api.admin_provider_runtime import WebchatRuntimeRoutingUpdate, provider_runtime_status, update_webchat_runtime_routing

_BUCKET_CONTRACT = "sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"

def _db_with_routing_rows(rows):
    db = Mock(); result = Mock(); result.mappings.return_value.all.return_value = list(rows); db.execute.return_value = result; return db

def _routing_row(*, canary_percent=5, kill_switch=False):
    return {"tenant_id":"tenant-a","channel_key":"website","primary_provider":"private_ai_runtime","canary_percent":canary_percent,"kill_switch":kill_switch,"enabled":True,"updated_at":datetime(2026,7,11,tzinfo=timezone.utc)}

def _stub(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr("app.api.admin_provider_runtime.get_provider_runtime_status", lambda db: {"ok":True,"status":"ready","warnings":[]})

def test_insert_rule_does_not_grant_default_authority(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    db=Mock(); result=Mock(); result.mappings.return_value.first.return_value=None; db.execute.return_value=result
    response=update_webchat_runtime_routing(WebchatRuntimeRoutingUpdate(canary_percent=100),db=db,current_user=Mock())
    rule=response["routing_rule"]; traffic=rule["traffic_selection"]
    assert response["ok"] is True and rule["canary_percent"]==100
    assert traffic["configured_mode"]=="control" and traffic["configuration_errors"]==[]
    assert db.commit.called

@pytest.mark.parametrize(("payload","code"),[(WebchatRuntimeRoutingUpdate(primary_provider="unexpected"),"primary_provider_not_allowed"),(WebchatRuntimeRoutingUpdate(fallback_providers=["unexpected"]),"fallback_provider_not_allowed")])
def test_rejection_uses_fixed_error_codes(monkeypatch,payload,code):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    with pytest.raises(HTTPException) as caught:
        update_webchat_runtime_routing(payload,db=Mock(),current_user=Mock())
    assert caught.value.detail=={"error_code":code}

def test_status_exposes_effective_authority(monkeypatch):
    _stub(monkeypatch); monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","shadow"); monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT","25"); monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH","false")
    response=provider_runtime_status(db=_db_with_routing_rows([]),current_user=Mock()); traffic=response["traffic_selection"]
    assert response["ok"] is True and traffic["configured_mode"]=="shadow" and traffic["canary_percent"]==25
    assert traffic["bucket_contract"]==_BUCKET_CONTRACT and traffic["webchat_runtime_rules"]["status"]=="ready"

def test_status_reports_database_rule_under_control_default(monkeypatch):
    _stub(monkeypatch); response=provider_runtime_status(db=_db_with_routing_rows([_routing_row()]),current_user=Mock())
    assert response["traffic_selection"]["configured_mode"]=="control"
    rule=response["traffic_selection"]["webchat_runtime_rules"]["items"][0]
    assert rule["database_canary_percent"]==5 and rule["database_kill_switch"] is False
    assert rule["effective_traffic_selection"]["configuration_errors"]==[]

@pytest.mark.parametrize(("canary","kill","code"),[(101,False,"provider_runtime_canary_percent_invalid"),(True,False,"provider_runtime_canary_percent_invalid"),(5,"false","provider_runtime_kill_switch_invalid")])
def test_invalid_persisted_rule_is_misconfigured(monkeypatch,canary,kill,code):
    _stub(monkeypatch); response=provider_runtime_status(db=_db_with_routing_rows([_routing_row(canary_percent=canary,kill_switch=kill)]),current_user=Mock())
    routing=response["traffic_selection"]["webchat_runtime_rules"]
    assert response["ok"] is False and response["status"]=="misconfigured"
    assert routing["reason_code"]=="provider_runtime_routing_rule_invalid" and routing["items"][0]["database_configuration_errors"]==[code]

def test_rule_query_failure_is_unavailable(monkeypatch):
    _stub(monkeypatch); db=Mock(); db.execute.side_effect=RuntimeError("db")
    response=provider_runtime_status(db=db,current_user=Mock())
    assert response["status"]=="unavailable" and response["traffic_selection"]["webchat_runtime_rules"]["status"]=="unavailable"

@pytest.mark.parametrize(("env","value","code"),[("PROVIDER_RUNTIME_TRAFFIC_MODE","invalid","provider_runtime_traffic_mode_invalid"),("PROVIDER_RUNTIME_CANARY_PERCENT","invalid","provider_runtime_canary_percent_invalid"),("PROVIDER_RUNTIME_KILL_SWITCH","invalid","provider_runtime_kill_switch_invalid")])
def test_invalid_global_configuration_fails_closed(monkeypatch,env,value,code):
    _stub(monkeypatch); monkeypatch.setenv(env,value)
    response=provider_runtime_status(db=_db_with_routing_rows([]),current_user=Mock())
    assert response["status"]=="misconfigured" and code in response["traffic_selection"]["configuration_errors"]

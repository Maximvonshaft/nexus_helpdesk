import json
from unittest.mock import Mock
import pytest
from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.health import ProviderHealthDecision, ProviderRuntimeHealth
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult

class DummyAdapter(ProviderAdapter):
    def __init__(self,name,result): self.name=name; self._result=result; self.calls=0
    async def generate(self,db,req): self.calls+=1; return self._result

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(provider_runtime_module,"_BOOTSTRAPPED",True); monkeypatch.setattr(ProviderRegistry,"_factories",{})
    for name in ("PROVIDER_RUNTIME_TRAFFIC_MODE","PROVIDER_RUNTIME_CANARY_PERCENT","PROVIDER_RUNTIME_KILL_SWITCH","PROVIDER_RUNTIME_PRIMARY_PROVIDER","PROVIDER_RUNTIME_FALLBACK_PROVIDERS","PROVIDER_RUNTIME_OUTPUT_CONTRACT","PROVIDER_RUNTIME_TIMEOUT_MS"):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","canary")

def _rule(canary_percent=100,kill_switch=False):
    return {"primary_provider":"private_ai_runtime","fallback_providers":[],"output_contract":"nexus_webchat_runtime_reply_v1","timeout_ms":3000,"kill_switch":kill_switch,"canary_percent":canary_percent}

def _db(rule):
    db=Mock(); query=Mock(); query.mappings.return_value.first.return_value=rule; audits=[]
    def execute(stmt,params,*a,**k):
        if "insert into provider_runtime_audit_logs" in str(stmt).lower(): audits.append(dict(params)); return Mock()
        return query
    db.execute.side_effect=execute; db.audit_rows=audits; return db

def _req(): return ProviderRequest(request_id="r",tenant_id="t",tenant_key="tk",channel_key="c",session_id="s",scenario="webchat_runtime_reply",body="hello",output_contract="nexus_webchat_runtime_reply_v1",timeout_ms=1000)
def _result(): return ProviderResult(ok=True,provider="private_ai_runtime",elapsed_ms=10,structured_output={"customer_reply":"hi","language":"en","intent":"greeting","handoff_required":False,"ticket_should_create":False})
def _summary(row): return json.loads(row["safe_summary"])

@pytest.mark.asyncio
async def test_missing_rule_defaults_control_zero(monkeypatch):
    monkeypatch.delenv("PROVIDER_RUNTIME_TRAFFIC_MODE",raising=False); db=_db(None); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req()); traffic=_summary(db.audit_rows[-1])["traffic_selection"]
    assert result.error_code=="provider_canary_control_path" and adapter.calls==0
    assert traffic["configured_mode"]=="control" and traffic["canary_percent"]==0

@pytest.mark.asyncio
async def test_authoritative_success_has_evidence():
    db=_db(_rule()); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req()); traffic=_summary(db.audit_rows[-1])["traffic_selection"]
    assert result.ok and adapter.calls==1 and traffic["path"]=="canary_authoritative" and traffic["authoritative"] is True

@pytest.mark.asyncio
async def test_zero_percent_suppresses_candidate():
    db=_db(_rule(0)); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req())
    assert result.error_code=="provider_canary_control_path" and adapter.calls==0

@pytest.mark.asyncio
async def test_shadow_discards_output(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","shadow"); db=_db(_rule()); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req())
    assert result.error_code=="provider_shadow_completed" and result.structured_output is None and adapter.calls==1

@pytest.mark.asyncio
async def test_kill_switch_wins_over_invalid_lower_settings(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH","true"); monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","invalid"); monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT","invalid")
    db=_db(_rule()); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req()); traffic=_summary(db.audit_rows[-1])["traffic_selection"]
    assert result.error_code=="kill_switch_active" and adapter.calls==0 and traffic["path"]=="kill_switch"

@pytest.mark.asyncio
@pytest.mark.parametrize(("env","value","code"),[("PROVIDER_RUNTIME_TRAFFIC_MODE","invalid","provider_runtime_traffic_mode_invalid"),("PROVIDER_RUNTIME_CANARY_PERCENT","invalid","provider_runtime_canary_percent_invalid"),("PROVIDER_RUNTIME_KILL_SWITCH","invalid","provider_runtime_kill_switch_invalid")])
async def test_invalid_config_fails_closed(monkeypatch,env,value,code):
    monkeypatch.setenv(env,value); db=_db(_rule()); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req())
    assert result.error_code==code and adapter.calls==0 and _summary(db.audit_rows[-1])["traffic_selection"]["execute_candidate"] is False

@pytest.mark.asyncio
async def test_env_override_cannot_mask_invalid_persisted_canary(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT","5"); db=_db(_rule(101)); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req())
    assert result.error_code=="provider_runtime_canary_percent_invalid" and adapter.calls==0

@pytest.mark.asyncio
async def test_env_override_cannot_mask_invalid_persisted_kill(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH","false"); db=_db(_rule(5,"false")); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req())
    assert result.error_code=="provider_runtime_kill_switch_invalid" and adapter.calls==0

@pytest.mark.asyncio
async def test_health_skip_and_terminal_failure_have_evidence(monkeypatch):
    monkeypatch.setattr(ProviderRuntimeHealth,"should_skip",lambda p:ProviderHealthDecision(skip=True,reason="cooldown",consecutive_failures=3)); db=_db(_rule()); adapter=DummyAdapter("private_ai_runtime",_result()); ProviderRegistry.register("private_ai_runtime",lambda db:adapter)
    result=await ProviderRuntimeRouter(db).route(_req())
    assert result.error_code=="all_providers_failed" and adapter.calls==0
    assert all("traffic_selection" in _summary(row) for row in db.audit_rows)

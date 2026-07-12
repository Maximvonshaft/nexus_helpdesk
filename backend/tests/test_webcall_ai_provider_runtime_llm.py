from __future__ import annotations
import json, os, sys
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import text
os.environ.setdefault("APP_ENV","development")
os.environ.setdefault("DATABASE_URL","sqlite:////tmp/webcall_ai_provider_runtime_llm_tests.db")
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT.parent))
from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.schemas import ProviderResult
from app.services.webcall_ai_production.config import get_webcall_ai_production_settings
from app.services.webcall_ai_production.providers.base import ProviderError
from app.services.webcall_ai_production.providers.provider_runtime_llm import ProviderRuntimeLLMProvider
from app.services.webcall_ai_production.providers.router import get_llm_provider

class Adapter(ProviderAdapter):
    name="private_ai_runtime"
    def __init__(self): self.requests=[]
    async def generate(self,db,request):
        self.requests.append(request)
        return ProviderResult(ok=True,provider=self.name,elapsed_ms=12,structured_output={"customer_reply":"Send the shipment reference.","language":"en","intent":"tracking_missing_number","tracking_number":None,"handoff_required":False,"handoff_reason":None,"recommended_agent_action":"ask_customer_for_tracking_number","ticket_should_create":False,"internal_summary":"Missing tracking number.","risk_flags":[]},raw_payload_safe_summary={"bridge_status":200})

def drop_tables():
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS provider_runtime_audit_logs")); c.execute(text("DROP TABLE IF EXISTS provider_routing_rules"))
def create_tables():
    with engine.begin() as c:
        c.execute(text("CREATE TABLE provider_routing_rules (id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36),channel_key VARCHAR(100),scenario VARCHAR(100),primary_provider VARCHAR(100),fallback_providers JSON,output_contract VARCHAR(100),timeout_ms INTEGER,canary_percent INTEGER,kill_switch BOOLEAN,enabled BOOLEAN,created_at DATETIME,updated_at DATETIME)"))
        c.execute(text("CREATE TABLE provider_runtime_audit_logs (id VARCHAR(36) PRIMARY KEY,tenant_id VARCHAR(36),provider VARCHAR(100),request_id VARCHAR(100),channel_key VARCHAR(100),session_id VARCHAR(100),operation VARCHAR(50),status VARCHAR(50),safe_summary JSON,error_code VARCHAR(255),elapsed_ms INTEGER,created_at DATETIME)"))
def insert_rule(db,canary=100,kill=0):
    db.execute(text("INSERT INTO provider_routing_rules (id,tenant_id,channel_key,scenario,primary_provider,fallback_providers,output_contract,timeout_ms,canary_percent,kill_switch,enabled) VALUES (:id,'default','webcall_ai','webcall_ai_decision','private_ai_runtime','[]','nexus_webchat_runtime_reply_v1',10000,:canary,:kill,1)"),{"id":str(uuid4()),"canary":canary,"kill":kill}); db.commit()

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    drop_tables(); Base.metadata.drop_all(bind=engine); Base.metadata.create_all(bind=engine); create_tables()
    for k,v in {"WEBCALL_AI_PRODUCTION_ENABLED":"true","WEBCALL_AI_AGENT_ENABLED":"true","STT_PROVIDER":"fake","LLM_PROVIDER":"provider_runtime","TTS_PROVIDER":"fake","WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER":"private_ai_runtime","WEBCALL_AI_PROVIDER_RUNTIME_TENANT_ID":"default","WEBCALL_AI_PROVIDER_RUNTIME_CHANNEL_KEY":"webcall_ai","WEBCALL_AI_PROVIDER_RUNTIME_SCENARIO":"webcall_ai_decision","PROVIDER_RUNTIME_TRAFFIC_MODE":"canary","PROVIDER_RUNTIME_CANARY_PERCENT":"100","PROVIDER_RUNTIME_KILL_SWITCH":"false"}.items(): monkeypatch.setenv(k,v)
    monkeypatch.setattr("app.services.provider_runtime.bootstrap_provider_runtime",lambda:None); get_webcall_ai_production_settings.cache_clear()
    yield
    drop_tables(); Base.metadata.drop_all(bind=engine); get_webcall_ai_production_settings.cache_clear()
@pytest.fixture
def db():
    s=SessionLocal(); yield s; s.close()
def audits(db):
    rows=db.execute(text("SELECT operation,status,safe_summary,error_code FROM provider_runtime_audit_logs ORDER BY created_at")).mappings().all(); out=[]
    for r in rows:
        summary=r["safe_summary"]; summary=json.loads(summary) if isinstance(summary,str) else summary
        out.append({**dict(r),"safe_summary":summary or {}})
    return out

def test_provider_selected(): assert isinstance(get_llm_provider("provider_runtime"),ProviderRuntimeLLMProvider)
def test_alias_routes_through_router(db):
    a=Adapter(); ProviderRegistry.register("private_ai_runtime",lambda s:a)
    result=ProviderRuntimeLLMProvider().respond("where is my parcel?",language="en")
    assert result.provider_name=="provider_runtime:private_ai_runtime" and len(a.requests)==1
    assert audits(db)[-1]["safe_summary"]["traffic_selection"]["authoritative"] is True

def test_sqlite_boolean_zero_normalized(db):
    insert_rule(db,100,0); a=Adapter(); ProviderRegistry.register("private_ai_runtime",lambda s:a)
    result=ProviderRuntimeLLMProvider().respond("where is my parcel?",language="en")
    assert result.provider_name=="provider_runtime:private_ai_runtime" and len(a.requests)==1
    assert audits(db)[-1]["safe_summary"]["traffic_selection"]["configuration_errors"]==[]

def test_control_suppresses_alias(monkeypatch,db):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","control"); a=Adapter(); ProviderRegistry.register("private_ai_runtime",lambda s:a)
    with pytest.raises(ProviderError) as e: ProviderRuntimeLLMProvider().respond("where?",language="en")
    assert e.value.code=="provider_canary_control_path" and a.requests==[]

def test_kill_suppresses_even_invalid_lower(monkeypatch,db):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH","true"); monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","invalid"); monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT","invalid")
    a=Adapter(); ProviderRegistry.register("private_ai_runtime",lambda s:a)
    with pytest.raises(ProviderError) as e: ProviderRuntimeLLMProvider().respond("where?",language="en")
    assert e.value.code=="kill_switch_active" and a.requests==[]

def test_unapproved_alias_fails_closed(monkeypatch,db):
    monkeypatch.setenv("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER","bad"); a=Adapter(); ProviderRegistry.register("private_ai_runtime",lambda s:a)
    with pytest.raises(ProviderError) as e: ProviderRuntimeLLMProvider().respond("where?",language="en")
    assert e.value.code=="provider_runtime_provider_not_allowed" and a.requests==[] and audits(db)==[]

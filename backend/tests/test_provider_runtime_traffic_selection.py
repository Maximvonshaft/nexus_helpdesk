import pytest
from app.services.provider_runtime.schemas import ProviderRequest
from app.services.provider_runtime.traffic_selection import ProviderTrafficPath, configured_traffic_mode, effective_canary_percent, effective_kill_switch, persisted_traffic_configuration_errors, safe_traffic_configuration, select_provider_traffic, stable_canary_bucket

BUCKET="sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"
def req(session_id="s"):
    return ProviderRequest(request_id="r",tenant_id="t",tenant_key="tk",channel_key="c",session_id=session_id,scenario="webchat_runtime_reply",body="hello",output_contract="nexus_webchat_runtime_reply_v1",timeout_ms=1000)
def for_bucket(target):
    for i in range(10000):
        item=req(f"s-{i}")
        if stable_canary_bucket(item)==target:return item
    raise AssertionError(target)

def test_missing_config_defaults_control_zero(monkeypatch):
    for n in ("PROVIDER_RUNTIME_TRAFFIC_MODE","PROVIDER_RUNTIME_CANARY_PERCENT","PROVIDER_RUNTIME_KILL_SWITCH"): monkeypatch.delenv(n,raising=False)
    s=safe_traffic_configuration(); d=select_provider_traffic(req(),canary_percent=s["canary_percent"],kill_switch=s["kill_switch"],configured_mode_value=s["configured_mode"])
    assert s["configured_mode"]=="control" and s["canary_percent"]==0 and d.path==ProviderTrafficPath.CONTROL

def test_boundaries():
    assert select_provider_traffic(req(),canary_percent=0,kill_switch=False,configured_mode_value="canary").path==ProviderTrafficPath.CONTROL
    for p in (1,5,25):
        assert select_provider_traffic(for_bucket(p-1),canary_percent=p,kill_switch=False,configured_mode_value="canary").authoritative
        assert not select_provider_traffic(for_bucket(p),canary_percent=p,kill_switch=False,configured_mode_value="canary").execute_candidate
    for b in (0,25,50,99): assert select_provider_traffic(for_bucket(b),canary_percent=100,kill_switch=False,configured_mode_value="canary").authoritative

def test_restart_stability_and_contract():
    a=req("stable"); b=ProviderRequest(**a.model_dump())
    assert stable_canary_bucket(a)==stable_canary_bucket(b)
    assert select_provider_traffic(a,canary_percent=25,kill_switch=False,configured_mode_value="canary")==select_provider_traffic(b,canary_percent=25,kill_switch=False,configured_mode_value="canary")
    assert safe_traffic_configuration()["bucket_contract"]==BUCKET

def test_shadow_and_control():
    shadow=select_provider_traffic(req(),canary_percent=100,kill_switch=False,configured_mode_value="shadow")
    control=select_provider_traffic(req(),canary_percent=100,kill_switch=False,configured_mode_value="control")
    assert shadow.execute_candidate and not shadow.authoritative
    assert not control.execute_candidate

def test_valid_kill_wins_over_invalid_lower_values():
    d=select_provider_traffic(req(),canary_percent="invalid",kill_switch=True,configured_mode_value="invalid")
    assert d.path==ProviderTrafficPath.KILL_SWITCH and not d.execute_candidate
    assert d.configuration_errors==("provider_runtime_canary_percent_invalid","provider_runtime_traffic_mode_invalid")

@pytest.mark.parametrize("value",["unknown","","   "])
def test_invalid_mode(value):
    with pytest.raises(ValueError,match="provider_runtime_traffic_mode_invalid"): configured_traffic_mode(value)
@pytest.mark.parametrize("value",["abc","-1","101","1.5"])
def test_invalid_env_percent(monkeypatch,value):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT",value)
    with pytest.raises(ValueError,match="provider_runtime_canary_percent_invalid"): effective_canary_percent(25)
@pytest.mark.parametrize("value",[-1,101,1.5,"01",True])
def test_invalid_direct_percent(value):
    with pytest.raises(ValueError,match="provider_runtime_canary_percent_invalid"): select_provider_traffic(req(),canary_percent=value,kill_switch=False,configured_mode_value="canary")
@pytest.mark.parametrize("value",["maybe","enabled","2",""])
def test_invalid_env_kill(monkeypatch,value):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH",value)
    with pytest.raises(ValueError,match="provider_runtime_kill_switch_invalid"): effective_kill_switch(False)
@pytest.mark.parametrize("value",["false",0,1,None])
def test_invalid_direct_kill(value):
    with pytest.raises(ValueError,match="provider_runtime_kill_switch_invalid"): select_provider_traffic(req(),canary_percent=25,kill_switch=value,configured_mode_value="canary")

def test_persisted_errors_ignore_env(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT","5"); monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH","true")
    assert persisted_traffic_configuration_errors(canary_percent=101,kill_switch="false")==["provider_runtime_canary_percent_invalid","provider_runtime_kill_switch_invalid"]

def test_safe_configuration_reports_all_errors(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE","unknown"); monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT","abc"); monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH","maybe")
    assert safe_traffic_configuration(default_canary_percent=25,default_kill_switch=False)["configuration_errors"]==["provider_runtime_traffic_mode_invalid","provider_runtime_canary_percent_invalid","provider_runtime_kill_switch_invalid"]

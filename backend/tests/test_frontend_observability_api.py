from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import frontend_observability as api
from app.api.deps import get_current_user


@pytest.fixture()
def client(monkeypatch):
    observed: list[tuple] = []
    monkeypatch.setattr(
        api,
        "record_web_vital",
        lambda name, rating, value: observed.append(("web_vital", name, rating, value)),
    )
    monkeypatch.setattr(
        api,
        "record_frontend_api_latency",
        lambda path, method, status, duration: observed.append(
            ("api_latency", path, method, status, duration)
        ),
    )
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    try:
        yield TestClient(app), observed
    finally:
        app.dependency_overrides.clear()


def test_frontend_metrics_are_authenticated_bounded_and_low_cardinality(client):
    http, observed = client
    response = http.post(
        "/api/observability/frontend-metrics",
        json={
            "metrics": [
                {
                    "kind": "web_vital",
                    "name": "LCP",
                    "rating": "good",
                    "value": 2100,
                },
                {
                    "kind": "api_latency",
                    "path": "/api/tickets/42",
                    "method": "GET",
                    "status": "200",
                    "duration_ms": 84,
                },
                {
                    "kind": "api_latency",
                    "path": "/api/webchat/conversations/wc_AbCd123/messages",
                    "method": "POST",
                    "status": "202",
                    "duration_ms": 120,
                },
            ]
        },
    )
    assert response.status_code == 204, response.text
    assert observed == [
        ("web_vital", "LCP", "good", 2.1),
        ("api_latency", "/api/tickets/:id", "GET", "200", 84.0),
        ("api_latency", "/api/webchat/conversations/:id/messages", "POST", "202", 120.0),
    ]


def test_frontend_metric_normalizes_inp_but_preserves_cls_score(client):
    http, observed = client
    response = http.post(
        "/api/observability/frontend-metrics",
        json={
            "metrics": [
                {"kind": "web_vital", "name": "INP", "rating": "good", "value": 180},
                {"kind": "web_vital", "name": "CLS", "rating": "good", "value": 0.08},
            ]
        },
    )
    assert response.status_code == 204, response.text
    assert observed == [
        ("web_vital", "INP", "good", 0.18),
        ("web_vital", "CLS", "good", 0.08),
    ]


def test_frontend_metric_rejects_query_strings_and_incomplete_payloads(client):
    http, observed = client
    unsafe = http.post(
        "/api/observability/frontend-metrics",
        json={
            "metrics": [
                {
                    "kind": "api_latency",
                    "path": "/api/tickets?customer_email=private@example.test",
                    "method": "GET",
                    "status": "200",
                    "duration_ms": 10,
                }
            ]
        },
    )
    assert unsafe.status_code == 422
    assert observed == []

    incomplete = http.post(
        "/api/observability/frontend-metrics",
        json={"metrics": [{"kind": "web_vital", "name": "CLS"}]},
    )
    assert incomplete.status_code == 422
    assert observed == []


def test_frontend_metric_batch_size_is_capped(client):
    http, observed = client
    response = http.post(
        "/api/observability/frontend-metrics",
        json={
            "metrics": [
                {
                    "kind": "web_vital",
                    "name": "CLS",
                    "rating": "good",
                    "value": 0.01,
                }
                for _ in range(51)
            ]
        },
    )
    assert response.status_code == 422
    assert observed == []

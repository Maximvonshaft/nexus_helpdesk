#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field, model_validator


LoginType = Literal["chatgptAuthTokens", "apiKey"]


class AccountLoginStartRequest(BaseModel):
    type: LoginType
    accessToken: str | None = Field(default=None, min_length=1)
    apiKey: str | None = Field(default=None, min_length=1)
    chatgptAccountId: str | None = Field(default=None)
    chatgptPlanType: str | None = Field(default=None)

    @model_validator(mode="after")
    def validate_credential_shape(self):
        if self.type == "chatgptAuthTokens" and not self.accessToken:
            raise ValueError("accessToken is required for chatgptAuthTokens")
        if self.type == "apiKey" and not self.apiKey:
            raise ValueError("apiKey is required for apiKey")
        return self


def _fingerprint(value: str) -> str:
    digest = hashlib.sha256(("nexus:codex:fixture:v1\0" + value).encode("utf-8", errors="ignore")).hexdigest()
    return "sha256:" + digest


def _safe_login_summary(request: AccountLoginStartRequest) -> dict[str, Any]:
    credential = request.accessToken if request.type == "chatgptAuthTokens" else request.apiKey
    return {
        "type": request.type,
        "credential_fingerprint": _fingerprint(credential or ""),
        "chatgpt_account_id_present": bool(request.chatgptAccountId),
        "chatgpt_plan_type_present": bool(request.chatgptPlanType),
    }


app = FastAPI(title="NexusDesk Codex App-Server Contract Fixture", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "service": "codex-app-server-contract-fixture"}


@app.post("/account/login/start")
def account_login_start(request: AccountLoginStartRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "sessionId": "fixture-session-" + str(int(time.time() * 1000)),
        "account": _safe_login_summary(request),
        "capabilities": {
            "replyTurn": False,
            "loginOnly": True,
        },
    }


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("CODEX_CONTRACT_FIXTURE_HOST") or "127.0.0.1"
    port = int(os.getenv("CODEX_CONTRACT_FIXTURE_PORT") or "18795")
    uvicorn.run("app_server_contract_fixture:app", host=host, port=port, reload=False)

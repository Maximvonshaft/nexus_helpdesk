import json
import os
import urllib.request
import urllib.error
import sqlite3
import time

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ROUTE_WORKER_E2E") != "1",
    reason="manual route-worker E2E script; requires live API on 127.0.0.1:8888 and local OpenClaw workspace DB",
)

BASE_URL = os.getenv("ROUTE_WORKER_E2E_BASE_URL", "http://127.0.0.1:8888/api")
DB_PATH = os.getenv("ROUTE_WORKER_E2E_DB_PATH", "/home/vboxuser/.openclaw/workspace/tgbot/nexusdesk/helpdesk_suite_lite/backend/helpdesk.db")


def request(method, path, data=None, token=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req_data = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8")) if response.length else None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise Exception(f"HTTPError {e.code} on {path}: {body}")


def test_routing():
    print(">> Logging in as admin...")
    login_res = request("POST", "/auth/login", {"username": "admin", "password": "demo123"})
    token = login_res["access_token"]

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Check if markets exist
    cur.execute("SELECT id, code FROM markets LIMIT 2")
    markets = cur.fetchall()
    market1_id = markets[0][0]

    # Insert test tickets
    timestamp = str(int(time.time()))
    cur.execute(f"INSERT INTO tickets (ticket_no, title, description, status, source_channel, priority, market_id, created_at, updated_at, team_id, source, source_chat_id) VALUES ('TKT-ROUTE-{timestamp}-1', 'Market Route', 'desc', 'new', 'whatsapp', 'high', ?, datetime('now'), datetime('now'), 1, 'manual', '+41798559737')", (market1_id,))
    ticket1_id = cur.lastrowid
    cur.execute(f"INSERT INTO tickets (ticket_no, title, description, status, source_channel, priority, market_id, created_at, updated_at, team_id, source, source_chat_id) VALUES ('TKT-ROUTE-{timestamp}-2', 'Global Route', 'desc', 'new', 'whatsapp', 'high', NULL, datetime('now'), datetime('now'), 1, 'manual', '+41798559737')")
    ticket2_id = cur.lastrowid
    conn.commit()

    print(">> Triggering messages via API (workflow-update)...")
    request("POST", f"/tickets/{ticket1_id}/outbound/send", {"channel": "whatsapp", "body": "Test market reply"}, token)
    request("POST", f"/tickets/{ticket2_id}/outbound/send", {"channel": "whatsapp", "body": "Test global reply"}, token)

    print(">> Waiting for worker to process messages...")
    time.sleep(3)

    print(">> Checking DB for OpenClawConversationLink (account_id routing)...")
    cur.execute("SELECT ticket_id, account_id FROM openclaw_conversation_links WHERE ticket_id IN (?, ?)", (ticket1_id, ticket2_id))
    links = cur.fetchall()
    print("Conversation links created by worker:", links)

    for ticket_id, account_id in links:
        if ticket_id == ticket1_id:
            assert account_id == "wa-mkt-live", f"Market ticket got {account_id}, expected wa-mkt-live"
        elif ticket_id == ticket2_id:
            assert account_id == "wa-global-live", f"Global ticket got {account_id}, expected wa-global-live"

    print(">> Verification complete!")


if __name__ == "__main__":
    if os.getenv("RUN_ROUTE_WORKER_E2E") != "1":
        raise SystemExit("Set RUN_ROUTE_WORKER_E2E=1 to run this manual E2E script")
    test_routing()

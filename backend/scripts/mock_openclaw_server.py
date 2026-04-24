#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / 'backend' / 'tests' / 'fixtures' / 'openclaw'
SENT_MESSAGES: list[dict] = []


def read_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, 'r', encoding='utf-8') as f:
        return json.load(f)


class Handler(BaseHTTPRequestHandler):
    server_version = 'NexusDeskOpenClawMock/1.0'

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json; charset=utf-8')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get('content-length') or '0')
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == '/healthz':
            self._send({'status': 'ok', 'mock': True})
            return
        if path == '/conversation_get':
            self._send(read_fixture('conversation.json'))
            return
        if path == '/messages_read':
            self._send(read_fixture('messages.json'))
            return
        if path == '/attachments_fetch':
            self._send(read_fixture('attachments.json'))
            return
        if path == '/events_poll' or path == '/events_wait':
            self._send(read_fixture('events.json'))
            return
        if path == '/sent_messages':
            self._send({'items': SENT_MESSAGES})
            return
        self._send({'error': 'not_found', 'path': path}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        if path == '/messages_send':
            if body.get('forceFailure'):
                self._send({'ok': False, 'error': 'forced mock failure'}, status=503)
                return
            route = body.get('route') or {}
            missing = [k for k in ('channel', 'recipient', 'accountId', 'threadId') if not route.get(k)]
            if missing:
                self._send({'ok': False, 'error': 'missing route fields', 'missing': missing}, status=400)
                return
            record = {
                'id': f'mock-sent-{len(SENT_MESSAGES)+1}',
                'sessionKey': body.get('sessionKey'),
                'body': body.get('body'),
                'route': route,
            }
            SENT_MESSAGES.append(record)
            self._send({'ok': True, 'message': record})
            return
        self._send({'error': 'not_found', 'path': path}, status=404)

    def log_message(self, fmt: str, *args) -> None:
        if os.environ.get('OPENCLAW_MOCK_VERBOSE') == '1':
            super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description='Deterministic mock OpenClaw server for NexusDesk smoke tests.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=int(os.environ.get('OPENCLAW_MOCK_PORT', '18792')))
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f'OpenClaw mock listening on http://{args.host}:{args.port}', flush=True)
    httpd.serve_forever()


if __name__ == '__main__':
    main()

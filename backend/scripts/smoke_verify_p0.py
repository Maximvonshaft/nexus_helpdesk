from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import io
import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("AUTO_INIT_DB", "true")
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("SECRET_KEY", "smoke-test-secret")
os.environ.setdefault("INTEGRATION_API_KEY", "smoke-int-key")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path('/tmp/helpdesk_smoke.db')}")

from app.main import app  # noqa: E402


def run() -> None:
    with TestClient(app) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/api/v1/integration/profile/+12345678901').status_code == 401
        assert client.get('/api/v1/integration/profile/+12345678901', headers={'X-API-Key': 'smoke-int-key'}).status_code == 200

        login = client.post('/api/auth/login', json={'username': 'lead', 'password': 'demo123'})
        assert login.status_code == 200, login.text
        token = login.json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}

        assert client.get('/api/lite/stream', headers=headers).status_code == 410
        blocked = client.post('/api/lite/cases', headers=headers, json={
            'issue_summary': 'x',
            'customer_request': 'y',
            'attachment_paths': ['/etc/hosts'],
        })
        assert blocked.status_code == 422, blocked.text

        create_case = client.post('/api/lite/cases', headers=headers, json={
            'issue_summary': 'test issue',
            'customer_request': 'test req',
            'customer_contact': '+19999999999',
        })
        assert create_case.status_code == 200, create_case.text
        case_id = create_case.json()['case']['id']

        upload = client.post(
            f'/api/tickets/{case_id}/attachments',
            headers=headers,
            files={'file': ('secret.txt', io.BytesIO(b'secret-data-123'), 'text/plain')},
            data={'visibility': 'internal'},
        )
        assert upload.status_code == 200, upload.text
        body = upload.json()
        assert 'file_path' not in body
        assert client.get(body['download_url'], headers=headers).status_code == 200
        outbound = client.post(
            f'/api/tickets/{case_id}/outbound/send',
            headers=headers,
            json={'channel': 'whatsapp', 'body': 'hello'},
        )
        assert outbound.status_code == 200, outbound.text
        assert outbound.json()['status'] == 'pending'

    print('P0 smoke verification passed')


if __name__ == '__main__':
    run()

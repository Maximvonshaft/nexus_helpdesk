import os
import re


def _location_block(content: str, path: str) -> str:
    match = re.search(rf'location\s*=\s*{re.escape(path)}\s*\{{([^}}]*)\}}', content)
    assert match is not None, f"Could not find {path} location block in nginx conf"
    return match.group(1)


def _timeout_seconds(block: str, directive: str) -> int:
    match = re.search(rf'{re.escape(directive)}\s+(\d+)s;', block)
    assert match is not None, f"Could not find {directive} seconds directive"
    return int(match.group(1))


def test_nginx_stream_canary_header_neutralization():
    nginx_conf_path = os.path.join(os.path.dirname(__file__), '../../deploy/nginx/default.conf')
    with open(nginx_conf_path, 'r') as f:
        content = f.read()
        
    block = _location_block(content, '/api/webchat/fast-reply/stream')
    
    # Assert header neutralization
    assert 'proxy_set_header X-Nexus-Stream-Canary "";' in block, "Canary header is not neutralized in stream route"
    
    # Assert streaming requirements are still there
    assert 'proxy_buffering off;' in block
    assert 'add_header X-Accel-Buffering "no" always;' in block
    assert 'add_header Cache-Control "no-store" always;' in block


def test_nginx_nonstream_fallback_timeout_is_safe_for_stream_rollback():
    nginx_conf_path = os.path.join(os.path.dirname(__file__), '../../deploy/nginx/default.conf')
    with open(nginx_conf_path, 'r') as f:
        content = f.read()

    block = _location_block(content, '/api/webchat/fast-reply')

    assert _timeout_seconds(block, 'proxy_read_timeout') >= 30
    assert 'proxy_buffering off;' in block
    assert 'add_header Cache-Control "no-store" always;' in block

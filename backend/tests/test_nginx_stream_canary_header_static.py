import os
import re

def test_nginx_stream_canary_header_neutralization():
    nginx_conf_path = os.path.join(os.path.dirname(__file__), '../../deploy/nginx/default.conf')
    with open(nginx_conf_path, 'r') as f:
        content = f.read()
        
    # Extract the block for the stream endpoint
    match = re.search(r'location\s*=\s*/api/webchat/fast-reply/stream\s*\{([^}]*)\}', content)
    assert match is not None, "Could not find stream location block in nginx conf"
    block = match.group(1)
    
    # Assert header neutralization
    assert 'proxy_set_header X-Nexus-Stream-Canary "";' in block, "Canary header is not neutralized in stream route"
    
    # Assert streaming requirements are still there
    assert 'proxy_buffering off;' in block
    assert 'add_header X-Accel-Buffering "no" always;' in block
    assert 'add_header Cache-Control "no-store" always;' in block

from app.services.canonical_route_projection import canonical_operator_href, project_control_tower_routes


def test_legacy_operator_hrefs_are_migrated_on_the_server():
    assert canonical_operator_href('/accounts') == '/channels'
    assert canonical_operator_href('/outbound-email?account=2') == '/channels'
    assert canonical_operator_href('/ai-control') == '/knowledge'
    assert canonical_operator_href('/bulletins') == '/control-tower'


def test_canonical_operator_hrefs_preserve_query_and_fragment():
    assert canonical_operator_href('/workspace?queue=ticket%3A1') == '/workspace?queue=ticket%3A1'
    assert canonical_operator_href('/runtime#audit') == '/runtime#audit'


def test_unknown_operator_href_fails_closed():
    assert canonical_operator_href('https://example.test') is None
    assert canonical_operator_href('/second-admin') is None


def test_control_tower_projection_disables_unknown_targets():
    payload = {
        'manager_actions': [
            {'key': 'valid', 'href': '/accounts', 'enabled': True},
            {'key': 'invalid', 'href': '/second-admin', 'enabled': True},
        ],
        'channel_health': [],
        'governance_lanes': [],
        'template_blocks': [],
    }
    projected = project_control_tower_routes(payload)
    assert projected['manager_actions'][0]['href'] == '/channels'
    assert projected['manager_actions'][0]['enabled'] is True
    assert projected['manager_actions'][1]['href'] is None
    assert projected['manager_actions'][1]['enabled'] is False

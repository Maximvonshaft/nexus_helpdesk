from __future__ import annotations

from app.services.canonical_route_projection import (
    canonical_operator_href,
    project_control_tower_routes,
)


def test_administration_and_account_are_canonical_operator_routes():
    assert canonical_operator_href('/administration') == '/administration'
    assert canonical_operator_href('/account') == '/account'


def test_rbac_governance_lane_projects_to_the_identity_control_plane():
    payload = {
        'governance_lanes': [
            {
                'key': 'rbac-lens',
                'area': '权限覆盖',
                'href': '/runtime',
                'enabled': True,
            },
            {
                'key': 'audit-safety',
                'area': '审计活跃',
                'href': '/runtime',
                'enabled': True,
            },
        ],
    }

    projected = project_control_tower_routes(payload)

    assert projected['governance_lanes'][0]['href'] == '/administration'
    assert projected['governance_lanes'][0]['enabled'] is True
    assert projected['governance_lanes'][1]['href'] == '/runtime'

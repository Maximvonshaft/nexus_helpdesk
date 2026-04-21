"""Manual smoke checklist for governance overlay round 4.
Run inside a real checkout after applying the overlay and merging patch.diff.
"""

CHECKS = [
    'GET /api/admin/users returns active and inactive users with capabilities',
    'PATCH /api/admin/users/{id} updates display_name/email/role/team/capabilities',
    'POST /api/admin/users/{id}/activate toggles account active',
    'POST /api/admin/users/{id}/deactivate blocks self-disable and last-admin-disable',
    'POST /api/admin/users/{id}/reset-password enforces min length',
    'Channel account create/update rejects invalid provider, self-fallback, missing fallback, and market mismatch',
    'process_outbound_message prefers explicit channel_account, then market route, then global fallback',
    'Unresolved OpenClaw events persist and can be listed, replayed, and dropped',
]

if __name__ == '__main__':
    for idx, item in enumerate(CHECKS, 1):
        print(f'{idx}. {item}')

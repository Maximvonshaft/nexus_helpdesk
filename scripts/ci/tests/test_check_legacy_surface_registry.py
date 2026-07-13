from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / 'scripts' / 'ci' / 'check_legacy_surface_registry.py'
REGISTRY_PATH = ROOT / 'config' / 'governance' / 'legacy-surface-domains.v1.json'

spec = importlib.util.spec_from_file_location('legacy_surface_registry', MODULE_PATH)
assert spec and spec.loader
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacySurfaceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = legacy.load_registry(REGISTRY_PATH)

    def raw_registry(self):
        return json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))

    def test_registry_contract_is_strict_and_current(self):
        self.assertEqual(self.registry['schema'], legacy.REGISTRY_SCHEMA)
        self.assertEqual(self.registry['registry_version'], '2026-07-13.1')
        self.assertEqual(self.registry['enforcement'], 'fail_closed')
        self.assertEqual(set(self.registry['allowed_dispositions']), legacy.ALLOWED_DISPOSITIONS)
        self.assertTrue(all(item['deletion_authorized'] is False for item in self.registry['domains']))

    def test_duplicate_domain_id_is_rejected(self):
        raw = self.raw_registry()
        raw['domains'].append(copy.deepcopy(raw['domains'][0]))
        with self.assertRaisesRegex(legacy.RegistryValidationError, 'domain_id_duplicate'):
            legacy.validate_registry(raw)

    def test_safe_to_remove_requires_prerequisites_and_never_authorizes_deletion(self):
        raw = self.raw_registry()
        candidate = raw['domains'][0]
        candidate['disposition'] = 'safe_to_remove'
        candidate['prerequisites'] = []
        with self.assertRaisesRegex(legacy.RegistryValidationError, 'safe_to_remove_requires_prerequisites'):
            legacy.validate_registry(raw)
        candidate['prerequisites'] = ['reference_proof']
        candidate['deletion_authorized'] = True
        with self.assertRaisesRegex(legacy.RegistryValidationError, 'deletion_authorized_must_be_false'):
            legacy.validate_registry(raw)

    def test_protected_domain_cannot_be_removable(self):
        raw = self.raw_registry()
        protected = next(item for item in raw['domains'] if item['id'] == 'protected_alembic_history')
        protected['disposition'] = 'safe_to_remove'
        with self.assertRaisesRegex(legacy.RegistryValidationError, 'protected_domain_disposition_invalid'):
            legacy.validate_registry(raw)

    def test_reachable_git_history_does_not_use_a_tracked_file_placeholder(self):
        self.assertNotIn('protected_reachable_git_history', {item['id'] for item in self.registry['domains']})
        result = legacy.scan_registry(self.registry, ['.gitignore'], read_text=lambda _: '')
        self.assertNotIn('protected_history', result['disposition_match_counts'])
        self.assertNotIn('565', result['owner_issue_match_counts'])

    def test_scan_protects_alembic_and_versioned_contracts(self):
        files = [
            'backend/alembic/versions/20260425_round_b_webchat.py',
            'backend/alembic/versions/20260601_0046_knowledge_runtime_v2.py',
            'webapp/design/frontend-product-foundation.v1.json',
            'backend/app/services/knowledge_runtime_v2/runtime.py',
        ]
        result = legacy.scan_registry(self.registry, files, read_text=lambda _: '')
        self.assertTrue(result['ok'])
        self.assertEqual(result['unowned_count'], 0)
        self.assertEqual(result['overlap_count'], 0)
        self.assertGreaterEqual(result['disposition_match_counts']['protected_history'], 2)
        self.assertGreaterEqual(result['disposition_match_counts']['active_authority'], 2)

    def test_versioned_contract_owner_matches_multi_digit_discovery(self):
        files = [
            'config/governance/example.v10.json',
            'webapp/design/example.v12.json',
            'backend/app/config/example.v123.json',
            'backend/evals/nested/example.v99.json',
            'config/governance/example.vbeta.json',
        ]
        result = legacy.scan_registry(self.registry, files, read_text=lambda _: '')
        self.assertTrue(result['ok'])
        self.assertEqual(result['unowned_count'], 0)
        self.assertEqual(result['overlap_count'], 0)
        self.assertEqual(result['owner_issue_match_counts']['650'], 4)

    def test_external_channel_path_matching_is_case_insensitive(self):
        result = legacy.scan_registry(
            self.registry,
            ['LOCAL_EXTERNAL_CHANNEL_READY_REPORT.md'],
            read_text=lambda _: '',
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['owner_issue_match_counts']['572'], 1)

    def test_external_channel_alembic_overlap_is_explicitly_allowed_and_protected(self):
        path = 'backend/alembic/versions/20260410_0005_round8_external_channel_markets.py'
        result = legacy.scan_registry(self.registry, [path], read_text=lambda _: '')
        self.assertTrue(result['ok'])
        self.assertEqual(result['overlap_count'], 0)
        self.assertEqual(result['owner_issue_match_counts']['532'], 1)
        self.assertEqual(result['owner_issue_match_counts']['572'], 1)
        self.assertEqual(result['disposition_match_counts']['protected_history'], 1)

    def test_non_allowed_owner_collision_fails_closed(self):
        raw = self.raw_registry()
        raw['domains'].append(
            {
                'id': 'synthetic_other_owner',
                'owner_issue': 999,
                'disposition': 'active_authority',
                'deletion_authorized': False,
                'rationale': 'Synthetic collision.',
                'prerequisites': ['review'],
                'selectors': {'paths': ['runtime/obsolete/handler.py'], 'globs': [], 'content_rules': []},
                'authoritative_refs': ['issue:#999'],
            }
        )
        raw['discovery_rules'].append(
            {
                'id': 'synthetic_collision_marker',
                'path_regex': '^runtime/obsolete/',
                'path_globs': [],
                'content_markers': [],
                'content_path_globs': [],
                'allowed_domain_ids': ['legacy_static_frontend'],
                'allow_multiple_domains': True,
            }
        )
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(registry, ['runtime/obsolete/handler.py'], read_text=lambda _: '')
        self.assertFalse(result['ok'])
        self.assertEqual(result['overlap_count'], 0)
        self.assertEqual(result['unowned_count'], 1)
        finding = result['findings'][0]
        self.assertEqual(finding['unexpected_domain_ids'], ['synthetic_other_owner'])

    def test_allowed_owner_plus_unexpected_owner_fails_as_outside_rule(self):
        raw = self.raw_registry()
        raw['domains'].append(
            {
                'id': 'synthetic_other_owner',
                'owner_issue': 999,
                'disposition': 'active_authority',
                'deletion_authorized': False,
                'rationale': 'Synthetic collision.',
                'prerequisites': ['review'],
                'selectors': {'paths': ['frontend/obsolete.js'], 'globs': [], 'content_rules': []},
                'authoritative_refs': ['issue:#999'],
            }
        )
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(registry, ['frontend/obsolete.js'], read_text=lambda _: '')
        self.assertFalse(result['ok'])
        self.assertEqual(result['overlap_count'], 1)
        finding = result['findings'][0]
        self.assertIn('legacy_surface_owner_outside_rule', finding['reason_codes'])
        self.assertEqual(finding['unexpected_domain_ids'], ['synthetic_other_owner'])
        self.assertEqual(finding['matched_domain_ids'], ['legacy_static_frontend', 'synthetic_other_owner'])

    def test_bounded_reader_never_requests_more_than_limit_plus_one(self):
        sizes = []
        class RecordingBytesIO(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)
        stream = RecordingBytesIO(b'x' * 128)
        with mock.patch.object(Path, 'open', return_value=stream):
            result = legacy._read_text_bounded(Path('/repo'), 'large.txt', max_bytes=16)
        self.assertIsNone(result)
        self.assertEqual(sizes, [17])

    def test_round_and_release_identity_do_not_leak_source_content(self):
        contents = {
            'backend/app/main.py': "app = FastAPI(version='20.4.0-round-b')",
            'ROUND25_HARDENING_REPORT.md': 'historical evidence',
            'deploy/docker-compose.server.yml': 'legacy-worker:\n  profiles: [legacy-worker]\n',
        }
        result = legacy.scan_registry(self.registry, contents, read_text=lambda path: contents.get(path))
        self.assertTrue(result['classification_complete'])
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn('historical evidence', encoded)
        self.assertNotIn('profiles:', encoded)
        self.assertEqual(result['findings'], [])

    def test_unowned_discovery_fails_closed_with_bounded_path_only_evidence(self):
        raw = self.raw_registry()
        raw['discovery_rules'].append(
            {
                'id': 'synthetic_unowned_marker',
                'path_regex': '^runtime/obsolete/',
                'path_globs': [],
                'content_markers': [],
                'content_path_globs': [],
                'allowed_domain_ids': ['legacy_static_frontend'],
                'allow_multiple_domains': False,
            }
        )
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(
            registry,
            ['runtime/obsolete/handler.py'],
            read_text=lambda _: 'secret-looking-content-must-not-appear',
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['unowned_count'], 1)
        finding = result['findings'][0]
        self.assertEqual(finding['path'], 'runtime/obsolete/handler.py')
        self.assertRegex(finding['path_sha256'], r'^[0-9a-f]{16}$')
        self.assertNotIn('secret-looking-content', json.dumps(result))

    def test_finding_output_is_deterministic_and_bounded(self):
        raw = self.raw_registry()
        raw['finding_limit'] = 2
        raw['discovery_rules'].append(
            {
                'id': 'synthetic_bounded_marker',
                'path_regex': '^orphan/',
                'path_globs': [],
                'content_markers': [],
                'content_path_globs': [],
                'allowed_domain_ids': ['legacy_static_frontend'],
                'allow_multiple_domains': False,
            }
        )
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(registry, ['orphan/c.py', 'orphan/a.py', 'orphan/b.py'], read_text=lambda _: None)
        self.assertEqual(result['finding_count'], 3)
        self.assertEqual(result['reported_finding_count'], 2)
        self.assertTrue(result['findings_truncated'])
        self.assertEqual([item['path'] for item in result['findings']], ['orphan/a.py', 'orphan/b.py'])

    def test_git_index_scan_excludes_symlinks_and_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / 'tracked.txt').write_text('ok', encoding='utf-8')
            (root / 'link.txt').symlink_to('tracked.txt')
            subprocess.run(['git', '-C', str(root), 'add', 'tracked.txt', 'link.txt'], check=True)
            self.assertEqual(legacy.collect_tracked_files(root), ['tracked.txt'])


if __name__ == '__main__':
    unittest.main()

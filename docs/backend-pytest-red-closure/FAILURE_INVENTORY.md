# Backend Pytest Failure Inventory

Initial failing count: **15 failed, 711 passed, 1 skipped**.
Final result: **731 passed, 1 skipped, 424 warnings**.

| Test File | Test Name | Category | Fix Scope | Root Cause | Final Status |
|---|---|---|---|---|---|
| `tests/test_codex_upstream_reply_transport.py` | multiple async reply transport tests | Async/test infra | Direct test update | Async marker did not match active test runtime. | Fixed |
| `tests/test_webchat_cards_migration.py` | migration upgrade/downgrade/re-upgrade | Migration reentrancy | Indirect migration fix | SQLite test downgrade path did not reliably drop expression/partial indexes before re-upgrade. | Fixed |
| `tests/test_webchat_stream_replay_safety.py` | replay invalid stored reply | Stream replay contract | Direct test and shared service fix | Replay event name and event ordering changed from legacy assertions. | Fixed |
| `tests/test_webchat_stream_replay_safety.py` | safe stored reply replay | Stream replay contract | Direct test and shared service fix | Test expected older replay/final/delta ordering. | Fixed |
| `tests/test_fastlane_p0_p2_closure_contracts.py` | stream ordering contract tests | Stream contract | Direct test and shared service fix | Contract drift: accepted ordering is now final before reply_delta. | Fixed |
| `tests/test_webchat_stream_replay_semantics.py` | done replay semantics | Stream replay contract | Direct test update | Replay final payload intentionally omits raw reply while reply text still appears in stream. | Fixed |
| `tests/test_webchat_stream_feature_flag.py` | stream feature flag tests | WebChat stream config | Direct test and shared API fix | Test doubles lacked newer settings attributes. | Fixed |
| `tests/test_webchat_codex_app_server_canary_observability.py` | canary/killswitch tests | Codex config | Direct test update | Tests did not explicitly set the feature flag needed for strict provider loading. | Fixed |
| `tests/test_webchat_codex_app_server_provider.py` | provider config tests | Codex config | Direct test update | Tests did not explicitly isolate the provider feature flag. | Fixed |
| `tests/test_webchat_fast_speedaf_enqueue.py` | Speedaf enqueue contract | Speedaf | Indirect shared API fix | Enqueue call signature drifted toward keyword-only invocation. | Fixed |
| `tests/test_webchat_stream_final_parse_failure.py` | invalid final parse | Stream parse contract | Indirect stream contract fix | Shared stream ordering/contract drift caused assertion mismatch. | Fixed |
| `tests/test_webchat_stream_flush_runtime_contract.py` | flush runtime contract | Stream contract | Indirect stream contract fix | Shared stream ordering/contract drift caused assertion mismatch. | Fixed |

Notes:
- The inventory covers the 15 initial failures by root-cause bucket; several failures were fixed indirectly by shared stream/API/migration changes rather than direct edits to each failing test file.
- No new skip or xfail was introduced.

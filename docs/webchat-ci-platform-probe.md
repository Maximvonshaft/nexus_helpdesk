# WebChat CI Platform Probe

Purpose: doc-only pull request used to determine whether GitHub Actions can execute jobs and expose logs independently of application-code changes.

This file intentionally does not change runtime, backend, frontend, deployment, migration, or workflow behavior.

Expected result:

- If Actions infrastructure is healthy, workflows should create jobs with visible steps/logs.
- If jobs still fail before steps/logs, the blocker is likely repository/Actions/runner/policy/logging level rather than PR #90 application code.

# CI Required Checks Policy

Required before merge:
- Python unit tests.
- Email-specific tests.
- Existing outbound regression tests.
- Typecheck/build for frontend if touched.
- Migration import/upgrade validation.
- Secret scan.
- Lint/format if project has standard command.

Block merge if:
- Email is enabled by default.
- Provider secrets appear in code.
- Tests rely on real SES credentials.
- Existing WhatsApp/WebChat tests fail.

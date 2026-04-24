# Workspace Refactor Plan

The current Workspace page is valuable but dense. Refactor safely by extracting components without changing APIs:

- `CaseQueue`
- `CaseHeader`
- `CustomerContext`
- `ConversationPanel`
- `BulletinPanel`
- `EvidencePanel`
- `CaseActionPanel`

Do not add direct-send behavior until the backend safety gate is fully verified.

## ADDED Requirements
### Requirement: Actionable structural capacity errors
When a managed client/server pair is structurally impossible, preflight and submission SHALL identify the affected service and selected pools and at least one configuration category, without disclosing credentials or volatile device state.
#### Scenario: Exclusive same card
- **WHEN** only the same card can host both and one request is exclusive
- **THEN** the error identifies the same-card exclusive conflict
#### Scenario: Node limits
- **WHEN** distinct cards on one node need more combined CPU, RAM or concurrency than configured
- **THEN** the error identifies those node limits
#### Scenario: Capacity of one side
- **WHEN** no candidate in a selected pool fits that side’s model, CPU, RAM or memory request
- **THEN** the error names the affected side and category
#### Scenario: Missing candidates
- **WHEN** the selected pool has no role-matching candidate
- **THEN** structural rejection does not turn an environment gap into a permanent capacity error

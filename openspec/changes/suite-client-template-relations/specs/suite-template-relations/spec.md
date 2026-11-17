## Purpose
让用户通过平台界面维护任务集合与多个客户端模板版本的可用关系，并在运行时选择一个模板，复用任务清单且保持历史执行可复现。
## ADDED Requirements
### Requirement: Editable many-to-many availability
The owner SHALL be able to update available client templates on existing draft and published suites without changing cases.
#### Scenario: Add to published suite
- **WHEN** the owner adds a compatible accessible template with the current revision
- **THEN** the relation is saved and the published case manifest remains unchanged
#### Scenario: Conflict or invalid relation
- **WHEN** a relation change uses a stale revision or an incompatible/inaccessible template
- **THEN** the entire change is rejected
### Requirement: Select a related client version
Runs SHALL select one currently related template and retain immutable execution snapshots.
#### Scenario: Reuse suite
- **WHEN** two runs select two different related templates
- **THEN** they use the same suite cases and their respective template snapshots
#### Scenario: Remove relation
- **WHEN** a template relation is removed
- **THEN** existing runs retain their snapshots and new submissions cannot select that template
### Requirement: Backward compatible relations
The system SHALL migrate existing associations and preserve multi-template relations in copy and export/import workflows.
#### Scenario: Existing single-template suite
- **WHEN** the platform upgrades
- **THEN** its original template remains available and older single-template API requests still work
#### Scenario: Single configuration entry
- **WHEN** a user changes which clients a collection can run
- **THEN** collection configuration provides template checkboxes and a save action, while the run wizard only selects one permitted template and no separate relationship management screen is required

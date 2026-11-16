# Evaluation results
## ADDED Requirements
### Requirement: Readable batch history
The platform SHALL show current-page status summaries, task and reported episode progress, scoped filtering and cross-page comparison in a responsive results list.
#### Scenario: Partial reporting
- **WHEN** a batch has only some episode reports
- **THEN** the list shows reported counts without inferring success from task completion.
### Requirement: Safe batch deletion
The platform SHALL logically delete only authorized batches whose tasks and managed services have stopped and completed cleanup, retaining audit records and refusing further retries.
#### Scenario: Busy batch
- **WHEN** deletion is requested with active tasks or uncleared resources
- **THEN** the API returns conflict and the batch remains visible.
#### Scenario: Deleted batch
- **WHEN** a cleared terminal batch is deleted after confirmation
- **THEN** it disappears from history and cannot be read through batch APIs or retried, while task audit data remains.

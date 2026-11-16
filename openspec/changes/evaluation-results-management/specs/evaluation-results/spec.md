# Evaluation results
## ADDED Requirements
### Requirement: Readable batch history
The platform SHALL show current-page status summaries, task and reported episode progress, scoped filtering and cross-page comparison in a responsive one-batch-per-row table with horizontal scrolling on narrow screens.
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

### Requirement: Collection management subtab
The results workspace SHALL expose run history and evaluation collection management as separate subtabs, reuse existing permissions and pass the chosen collection to the launch wizard.
#### Scenario: Switch while editing
- **WHEN** the user edits a collection draft and switches to history and back
- **THEN** the draft remains intact and launching the collection opens the wizard with that collection selected.

### Requirement: Collapsible collection configuration
The collection manager SHALL display compact rows and a collapsible configuration area, preserving unsaved edits when collapsed.
#### Scenario: Collapse an unsaved draft
- **WHEN** the user edits a draft, collapses configuration and expands it again
- **THEN** the draft values remain intact while the collection list stays available.

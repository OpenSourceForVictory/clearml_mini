## ADDED Requirements
### Requirement: Scoped machine configuration tabs
The UI SHALL display machine settings in named functional tabs and preserve unsaved values while switching tabs on the same machine.
#### Scenario: Switch configuration sections
- **WHEN** an administrator edits CPU capacity, opens storage and returns to basic configuration
- **THEN** the entered capacity remains and unrelated settings are hidden
### Requirement: Read-only registration layout
Template configuration SHALL use the registration layout without allowing writes or horizontal page overflow.
#### Scenario: Inspect a long template
- **WHEN** the user inspects a template containing long commands and paths at desktop or mobile width
- **THEN** the page stays within viewport width, fields show the saved version and values, and no registration action is available
### Requirement: Separate evaluation submission and results
The UI SHALL provide a business evaluation wizard solely for submitting evaluations and a following navigation entry for evaluation runs and results; My Tasks SHALL contain no submission form.
#### Scenario: Inspect a submitted batch
- **WHEN** the user opens a batch from evaluation runs and results
- **THEN** task states, saved runtime parameters, server states and evaluation results are available without mounting registration management
#### Scenario: Refresh a batch
- **WHEN** the user refreshes an existing batch detail URL
- **THEN** the results navigation remains selected and returning opens the batch list
### Requirement: Preserve preparation access
Collection and client preparation SHALL remain accessible outside the submission wizard with existing authorization boundaries.
#### Scenario: Publish and run a collection
- **WHEN** an authorized user publishes a collection and selects run
- **THEN** the wizard opens with that collection selected

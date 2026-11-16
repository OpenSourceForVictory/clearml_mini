## ADDED Requirements
### Requirement: Ordinary command authoring
The UI SHALL let administrators register ordinary command client templates with direct integration and no episode reporting, for Docker and host execution.
#### Scenario: Select command task
- **WHEN** a client template is configured as an ordinary command
- **THEN** its submission uses task_type command, integration_mode direct and episode_reporting false, with exit-code guidance.
### Requirement: Submit ordinary commands
The collection editor and launch wizard SHALL support one execution per command case without requiring inference service addresses or fabricated evaluation results.
#### Scenario: Command submission
- **WHEN** a user chooses a command template and submits custom cases
- **THEN** the wizard defaults to no service and emits compatibility episodes=1 per case while displaying zero evaluation rounds.

## ADDED Requirements
### Requirement: Image availability is advisory during enrollment
Docker node enrollment SHALL require a usable Docker daemon but SHALL NOT require a runner image, pull images, build images or run image-based acceptance containers.
#### Scenario: Runner image absent
- **WHEN** Docker is available but the configured runner image is missing
- **THEN** enrollment SHALL proceed and display the image reference and a manual preparation command without claiming container runtime verification
#### Scenario: Custom image
- **WHEN** a custom runner is configured
- **THEN** guidance SHALL use that exact image and SHALL NOT substitute the default runner
### Requirement: Task image readiness remains enforced
The system SHALL retain task-specific image approval and readiness checks independently of node enrollment.
#### Scenario: Business image missing
- **WHEN** an enrolled node lacks the image required by a container template
- **THEN** the scheduler SHALL reject that node as ineligible for the task until the required image is approved and ready

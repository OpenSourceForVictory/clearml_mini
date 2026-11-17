## ADDED Requirements

### Requirement: Physical pool accelerator guidance
The resource pool interface SHALL explicitly identify physical pools as supporting GPU and NPU without implying a new NPU-only membership constraint.

#### Scenario: Create a physical pool
- **WHEN** an administrator opens pool creation
- **THEN** the physical option identifies GPU and NPU and submits the existing wildcard model value

#### Scenario: Create a simulated pool
- **WHEN** an administrator selects simulated devices
- **THEN** the existing virtual value is submitted

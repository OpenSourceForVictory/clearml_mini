## ADDED Requirements
### Requirement: Resource display uses admission freshness
The administrator resource listing SHALL report GPU telemetry freshness using the same sample time and field checks as scheduling admission.
#### Scenario: Valid sample
- **WHEN** a GPU sample has all required values and is younger than 15 seconds
- **THEN** the listing marks it fresh
#### Scenario: Invalid sample
- **WHEN** a sample is old, future-dated, or lacks required values
- **THEN** the listing marks it stale

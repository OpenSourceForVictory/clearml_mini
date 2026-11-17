## ADDED Requirements

### Requirement: Admission accounts for external memory use
For a device or host with fresh memory telemetry, admission SHALL enforce both configured total capacity and observed available memory. It SHALL charge all active or uncleared platform reservations plus the new request against observed available memory when per-task physical usage attribution is unavailable. The same calculation SHALL apply in read-only preflight and scheduling.

#### Scenario: External process occupies device memory
Given a shared device with 48 GiB total, 28 GiB observed free, one running platform task reserving 20 GiB, and a second task requesting 20 GiB, the second task remains pending even though total platform reservations would fit the physical total.

#### Scenario: Capacity later returns
Given the same reservations and a fresh sample with 48 GiB free, the second task may be admitted if all other limits also pass.

#### Scenario: External process occupies host memory
Given fresh host telemetry whose available memory is less than existing platform RAM reservations plus the new request, admission reports node RAM busy even when the configured total RAM budget is sufficient.

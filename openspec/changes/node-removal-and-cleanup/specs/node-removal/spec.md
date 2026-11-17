## ADDED Requirements
### Requirement: Safe machine removal
An administrator SHALL delete only an unmanaged machine without pending or active work, unconfirmed cleanup, unresolved upgrade, or running remote cleanup.
#### Scenario: Remove and register again
- **WHEN** an eligible machine is deleted
- **THEN** it and its devices disappear from active lists, original names and device identities may be registered anew, and historical foreign keys and audit records remain
#### Scenario: Stale or unsafe removal
- **WHEN** revision differs or prerequisites fail
- **THEN** deletion is rejected without changing records
### Requirement: Reviewed SSH cleanup
The platform SHALL support SSH cleanup with temporary credentials, host fingerprint confirmation, a read-only plan and explicit execution confirmation.
#### Scenario: Known root installation
- **WHEN** the node is unmanaged and the confirmed plan still matches trusted installation files
- **THEN** components are stopped and installation files moved to a private backup with a manifest, preserving business assets, images and accounts
#### Scenario: Unsafe or partial cleanup
- **WHEN** identity, paths, journals, services, revision or execution changes make cleanup unsafe
- **THEN** cleanup stops, exposes a sanitized reason and preserves recovery evidence without claiming success
### Requirement: Independent actions
Deleting the platform record SHALL NOT perform remote file deletion.
#### Scenario: Offline machine deletion
- **WHEN** an offline unmanaged machine is otherwise eligible
- **THEN** its platform record can be removed with explicit notice that node files remain

### Requirement: Current authorization and durable cleanup receipts
The executor SHALL use the current administrator authorization on trust and execution confirmation and retain unacknowledged cleanup outcomes.
#### Scenario: Browser renewed during review
- **WHEN** the administrator confirms with a renewed login token
- **THEN** subsequent control-plane calls use that token without persisting it or bypassing expiration
#### Scenario: Cleanup callback fails
- **WHEN** a remote outcome cannot be acknowledged by the platform
- **THEN** the executor retains it beyond normal operation retention until a later authenticated reconciliation

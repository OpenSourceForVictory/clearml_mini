## ADDED Requirements

### Requirement: A multi-device machine can configure storage per device

The platform SHALL distinguish a machine-wide default from a device-specific storage override. It SHALL send each Worker slot its resolved configuration and validate its acknowledgement against that slot's expected directory. A device edit SHALL preserve other device overrides and the machine-wide default.

#### Scenario: Machine has only per-device bootstrap directories
- **WHEN** a machine has no platform-wide storage configuration
- **THEN** the machine-wide editor remains empty and each device editor shows that device's actual directory

#### Scenario: Administrator changes one device
- **WHEN** an administrator saves a device-specific absolute directory with the current storage revision
- **THEN** only that device receives the new directory intent, its effective work directory retains a unique slot suffix, and other devices retain their selected base configuration

#### Scenario: Revision conflict or incorrect acknowledgement
- **WHEN** a stale editor attempts to save or a Worker reports a different effective directory
- **THEN** the platform rejects the request or acknowledgement without marking the device ready

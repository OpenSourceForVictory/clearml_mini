# Design

New enrollment continues to require an explicit host, docker, or both mode. Re-enrollment defaults to `preserve`; the UI offers explicit host, docker, or both overrides. The onboarding contract accepts `preserve` only for an existing node. Probe checks Docker and host resource requirements for the effective selection, including preserved per-device modes. The installer applies an explicit mode list to each original Worker config in the same rollback-protected transaction used for credential and sharing changes. Worker registration then reports the updated modes to the scheduler.

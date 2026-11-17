## ADDED Requirements
### Requirement: Bounded authenticated renewal
The platform SHALL renew unexpired login tokens for active accounts with a 30-minute token lifetime capped at 12 hours from the original login, without resetting that origin.
#### Scenario: Active session renews
- **WHEN** an authenticated active user renews a valid session
- **THEN** a replacement token preserves its original login time and has the bounded expiry
#### Scenario: Invalid session cannot renew
- **WHEN** the token is expired, lacks a session origin, has an invalid origin, or the account is disabled
- **THEN** renewal is rejected with 401
### Requirement: Real interaction drives renewal
The browser SHALL renew only following trusted visible user interaction, throttle renewal, and lock after 30 minutes idle or token expiry.
#### Scenario: Polling without interaction
- **WHEN** background polling continues without user input
- **THEN** polling does not renew the session or reset idle time
#### Scenario: Network error and concurrent requests
- **WHEN** renewal fails or an old response arrives after credentials changed
- **THEN** drafts remain mounted and old responses do not replace or clear newer credentials
### Requirement: Recover drafts after expiry
The browser SHALL hide and block the workspace on expiry while preserving mounted forms until same-account reauthentication or explicit logout.
#### Scenario: Same user resumes
- **WHEN** the same account with the same role logs in again
- **THEN** the current form and navigation remain available without replaying failed writes
#### Scenario: Different account or explicit logout
- **WHEN** another account tries to resume or the user chooses logout
- **THEN** another account cannot access the retained workspace and explicit logout discards it

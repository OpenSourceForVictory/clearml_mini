## MODIFIED Requirements

### Requirement: Named client runtime blockers
Preflight SHALL name the requested client environment or image on each candidate device and distinguish an unapproved resource, a missing image, a failed resource check, an unapplied configuration, and a missing or expired report where the saved Worker data supports that distinction. It SHALL show a specific action and SHALL NOT disclose raw Worker errors, host paths, or credentials to the task owner.

#### Scenario: Approved image absent from node
Given an approved client image and a failed image inspection reporting that it does not exist, when preflight runs, then the candidate-device reason names the image, states that the node lacks it, and directs the user to prepare the business image on that node.

#### Scenario: Unapproved image
Given a client image absent from the administrator-approved node config, when preflight runs, then the candidate-device reason names the image and asks for approval in the node container environment.

#### Scenario: Private probe detail
Given a failed Worker resource report containing a private filesystem path, when preflight runs, then the candidate-device reason reports only a safe failure category and action; the private path is absent from the response.

## 1. Measurement
- [x] 1.1 Add full-source configuration and directory/function reporting with weighted/missing-file/gate tests and documentation.
- [x] 1.2 Add reproducible runner and CI artifact/gates; verify failure propagation, update developer guide and Skill.
- [x] 1.3 Fix full-suite protocol registration and SDK bundle regressions with explicit contract/package assertions and validation notes.
## 2. Full functional acceptance
- [x] 2.1 Extend meaningful normal/failure/permission tests across template, suite, execution/results, scheduling and node modules to meet required coverage floors; fix encountered defects and record any decisions requiring user confirmation.
- [x] 2.2 Run all browser tests and isolated API/database integration tests, add missing cross-module workflow scenarios and document coverage/real-machine boundaries.
## 3. Baseline
- [x] 3.1 Execute full isolated suite, record per-directory baseline and honest verification boundaries, validate OpenSpec and commit.

## Evidence / completion
Fresh isolated full suite: 1112 passed, 99 skipped, no failures; coverage runner exits 0. Python line/branch coverage 89.35% / 78.16%; scripts 80.13% / 77.46%. All six directory and three critical-function gates pass. Third round adds 104 cases, including real PostgreSQL migration drivers using per-test databases. See docs/validation/project-coverage/README.md for prior browser acceptance, failures fixed, source isolation and hardware/virtual-time limitations. No production deployment; pending N:N suite changes remain separate.

## 4. Current-tree coverage refresh (2026-11-24)
- [x] 4.1 Add release backup/restore success, timeout, data-preservation and cleanup failure-path assertions without invoking Docker.
- [x] 4.2 Run the full current-source Python denominator with isolated PostgreSQL, supplement coverage from new tests on the same source tree, retain skips/failures, and document directory/function gates.

2026-11-24 refresh: 2663 full-suite passes + 16 new distinct passes, 132 skips retained; combined current-source coverage 92.57% / 83.87%, all gates pass. Evidence: docs/validation/coverage-complete-20261124/README.md.

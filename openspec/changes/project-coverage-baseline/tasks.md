## 1. Measurement
- [x] 1.1 Add full-source configuration and directory/function reporting with weighted/missing-file/gate tests and documentation.
- [x] 1.2 Add reproducible runner and CI artifact/gates; verify failure propagation, update developer guide and Skill.
- [x] 1.3 Fix full-suite protocol registration and SDK bundle regressions with explicit contract/package assertions and validation notes.
## 2. Full functional acceptance
- [ ] 2.1 Extend meaningful normal/failure/permission tests across template, suite, execution/results, scheduling and node modules to meet required coverage floors; fix encountered defects and record any decisions requiring user confirmation.
- [x] 2.2 Run all browser tests and isolated API/database integration tests, add missing cross-module workflow scenarios and document coverage/real-machine boundaries.
## 3. Baseline
- [x] 3.1 Execute full isolated suite, record per-directory baseline and honest verification boundaries, validate OpenSpec and commit.

## Evidence / remaining gap
833 Python full-suite passes, 121 skips; 231 new cases pass independently; PostgreSQL 22 passes/25 skips; browser 80 passes/9 skips plus 3 isolated real-API management tests. See docs/validation/project-coverage/README.md. Second round: 75 new cases, 129 related regression passes. Worker and all critical groups now meet their floors; task 2.1 remains open because scripts are still 46.03% line / 48.63% branch. Combined Python coverage is 80.59% line / 71.68% branch. No production deployment.

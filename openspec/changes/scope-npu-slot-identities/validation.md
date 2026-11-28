# Validation

- `python -m pytest backend/tests/test_slot_identity_scope.py backend/tests/test_enrollment.py backend/tests/test_memberships.py -q`: 10 passed. Covers cross-node NPU identity, same-node rejection, global GPU rejection, registration idempotence, and existing enrollment/membership behavior.
- `python -m pytest backend/tests/test_slot_identity_scope.py -q`: 3 passed. Includes a disposable SQLite migration from the old index with retained slot row, duplicate NPU insertion, rejected same-node NPU and duplicate GPU, and downgrade refusal.
- `git diff --check`: passed.

These are API and disposable database checks. No live NPU enrollment or production PostgreSQL migration was performed, so the reported remote timeout remains a likely consequence rather than a confirmed field diagnosis. The project-wide coverage gate and full test suite were not rerun for this narrowly scoped change.

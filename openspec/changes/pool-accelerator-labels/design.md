# Design
Keep the existing physical versus simulated pool contract. Explicitly label physical devices GPU / NPU, and explain that task accelerator requirements control dispatch. A dedicated NPU pool is organized through its members, not a new type constraint.

# Validation
Browser creation payload checks for physical and simulated pools, plus membership and accelerator scheduling regression tests.

# Slot identity

## Requirements

- Two different nodes MAY register NPU devices with the same Ascend PCI/chip identity.
- One node MUST NOT register that NPU identity for two different slots.
- Two nodes MUST NOT register the same GPU UUID for live devices.
- Re-registering the same slot MUST return the original slot ID.
- Soft-deleted slots MUST NOT block registration.

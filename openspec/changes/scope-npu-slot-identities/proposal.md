# Scope NPU slot identities to the node

## Problem

Ascend reports `ascend:<PCI BDF>:<chip>` as `gpu_uuid`. PCI addresses can repeat on different hosts, while the current live-slot index and registration lookup require global uniqueness. The second host can receive 409 during registration and subsequently time out in enrollment.

## Scope

Keep GPU UUID globally unique. Make NPU identity unique within a node, without changing slot IDs or Worker device reporting. Apply a database index migration and document deployment order.

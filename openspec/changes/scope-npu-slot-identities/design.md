# Design

Registration uses accelerator type to choose identity scope: NPU lookup includes `node_name`, GPU lookup remains global. Partial unique indexes enforce the same rule for live slots. `(node_name, gpu_index)` remains unique for every accelerator. Enrollment already filters by Worker ID and compares expected index/identity pairs, so no change is required there. Downgrade refuses to recreate the old global index when live duplicate identities exist.

"""Public placement metadata from an actually assigned execution slot."""
def view(slot):
    if slot is None:
        return None
    return {key: getattr(slot, key) for key in
            ('id', 'node_name', 'accelerator_type', 'gpu_index', 'gpu_uuid', 'gpu_model')}

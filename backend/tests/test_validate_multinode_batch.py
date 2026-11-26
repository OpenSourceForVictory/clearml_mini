import pytest

from scripts.validate_multinode_batch import max_overlap


def test_overlap_counts_tasks_and_distinct_nodes():
    intervals = [
        ('node-a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:05Z'),
        ('node-a', '2026-01-01T00:00:01Z', '2026-01-01T00:00:04Z'),
        ('node-b', '2026-01-01T00:00:02Z', '2026-01-01T00:00:03Z'),
    ]
    assert max_overlap(intervals) == (3, 2)
    assert max_overlap([]) == (0, 0)
    assert max_overlap([('node-a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')]) == (0, 0)


def test_touching_intervals_do_not_overlap_and_reversed_is_rejected():
    assert max_overlap([
        ('node-a', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z'),
        ('node-b', '2026-01-01T00:00:01Z', '2026-01-01T00:00:02Z'),
    ]) == (1, 1)
    with pytest.raises(ValueError, match='finished before'):
        max_overlap([('node-a', '2026-01-01T00:00:02Z', '2026-01-01T00:00:01Z')])

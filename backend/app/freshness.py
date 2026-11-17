"""Freshness windows for node reports and independently sampled resources."""
from datetime import timedelta, timezone

ENVIRONMENT_REPORT_SECONDS = 60
RESOURCE_SAMPLE_SECONDS = 45


def recent(value, at, seconds):
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return timedelta(0) <= at - value < timedelta(seconds=seconds)

from scripts.generate_data_stream import _hour_partition
from datetime import datetime, timezone


def test_hour_partition_is_hierarchical():
    dt = datetime(2026, 6, 25, 14, 0, 0, tzinfo=timezone.utc)
    result = _hour_partition(dt)
    assert result == "year=2026/month=06/day=25/hour=14", f"Got: {result}"


def test_hour_partition_single_digit():
    dt = datetime(2026, 1, 5, 3, 0, 0, tzinfo=timezone.utc)
    result = _hour_partition(dt)
    assert result == "year=2026/month=01/day=05/hour=03", f"Got: {result}"

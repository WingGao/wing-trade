from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.fixture
def executed_at() -> datetime:
    return datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)

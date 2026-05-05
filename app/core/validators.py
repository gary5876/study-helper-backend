"""Input validators for API security."""
from __future__ import annotations

import uuid as _uuid


def validate_api_key(key: str, plan: str) -> bool:
    """Validate API key format by provider plan."""
    if not key or len(key) < 20:
        return False
    if plan == "paid":
        return key.startswith("sk-ant-")
    if plan == "gpt":
        return key.startswith("sk-")
    if plan == "timely":
        return len(key) >= 20
    return False


def is_valid_uuid(value: str) -> bool:
    """Check if string is a valid UUID. Returns False for None/non-str inputs."""
    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False

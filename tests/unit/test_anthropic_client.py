"""Unit tests for the Anthropic client wrapper."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.exceptions import GenerationError
from app.services.anthropic_client import generate_with_retry
from app.services.json_utils import extract_json


# ─────────────────────────────────────────
# extract_json (json_utils)
# ─────────────────────────────────────────

def test_extract_json_plain():
    raw = '{"key": "value"}'
    assert extract_json(raw) == {"key": "value"}


def test_extract_json_with_markdown_fence():
    raw = '```json\n{"key": "value"}\n```'
    assert extract_json(raw) == {"key": "value"}


def test_extract_json_with_plain_fence():
    raw = '```\n{"key": "value"}\n```'
    assert extract_json(raw) == {"key": "value"}


def test_extract_json_invalid_raises():
    with pytest.raises(GenerationError) as exc:
        extract_json("not valid json at all")
    assert exc.value.status_code == 500


# ─────────────────────────────────────────
# generate_with_retry — mocked _call_claude
# ─────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.services.anthropic_client._call_claude")
async def test_generate_with_retry_success(mock_call):
    mock_call.return_value = '{"result": "ok"}'
    result = await generate_with_retry("sk-ant-fake", "sys", "user", max_retries=0)
    assert result == {"result": "ok"}
    assert mock_call.call_count == 1


@pytest.mark.asyncio
@patch("app.services.anthropic_client._call_claude")
async def test_generate_with_retry_retries_on_500(mock_call):
    """Should retry on 500-level GenerationError and eventually succeed."""
    mock_call.side_effect = [
        GenerationError("Server error", status_code=500),
        '{"result": "recovered"}',
    ]
    result = await generate_with_retry("sk-ant-fake", "sys", "user", max_retries=1)
    assert result == {"result": "recovered"}
    assert mock_call.call_count == 2


@pytest.mark.asyncio
@patch("app.services.anthropic_client._call_claude")
async def test_generate_with_retry_no_retry_on_401(mock_call):
    """Should NOT retry on auth errors."""
    mock_call.side_effect = GenerationError("Bad key", status_code=401)
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("sk-ant-fake", "sys", "user", max_retries=2)
    assert exc.value.status_code == 401
    assert mock_call.call_count == 1  # no retry


@pytest.mark.asyncio
@patch("app.services.anthropic_client._call_claude")
async def test_generate_with_retry_exhausted(mock_call):
    """Should raise after all retries exhausted."""
    mock_call.side_effect = GenerationError("Server error", status_code=500)
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("sk-ant-fake", "sys", "user", max_retries=1)
    assert exc.value.status_code == 500
    assert mock_call.call_count == 2  # 1 initial + 1 retry


@pytest.mark.asyncio
@patch("app.services.anthropic_client._call_claude")
async def test_generate_with_retry_calls_on_attempt_callback(mock_call):
    mock_call.return_value = '{"ok": true}'
    attempts_seen = []
    await generate_with_retry(
        "sk-ant-fake", "sys", "user",
        max_retries=0,
        on_attempt=lambda n: attempts_seen.append(n),
    )
    assert attempts_seen == [0]

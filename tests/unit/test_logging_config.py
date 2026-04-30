"""Unit tests for app/core/logging_config.py — _JsonFormatter + configure_logging branches."""
from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest

from app.core.logging_config import _JsonFormatter, configure_logging


def _make_record(level=logging.INFO, msg="hello", **extras):
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname="x.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    for k, v in extras.items():
        setattr(record, k, v)
    return record


def test_formatter_outputs_valid_json():
    fmt = _JsonFormatter()
    out = fmt.format(_make_record())
    parsed = json.loads(out)
    assert parsed["message"] == "hello"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert "timestamp" in parsed


def test_formatter_includes_extra_fields():
    fmt = _JsonFormatter()
    record = _make_record(request_id="req-abc", user_id="u-1")
    out = fmt.format(record)
    parsed = json.loads(out)
    assert parsed["request_id"] == "req-abc"
    assert parsed["user_id"] == "u-1"


def test_formatter_includes_exception_info():
    fmt = _JsonFormatter()
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys
        record = logging.LogRecord(
            name="x", level=logging.ERROR, pathname="p", lineno=1,
            msg="failed", args=(), exc_info=sys.exc_info(),
        )
    out = fmt.format(record)
    parsed = json.loads(out)
    assert "exception" in parsed
    assert "RuntimeError" in parsed["exception"]
    assert "boom" in parsed["exception"]


def test_formatter_message_with_args():
    """logger.info('user %s did %s', 'alice', 'login') 같은 % 포맷팅 처리."""
    record = logging.LogRecord(
        name="x", level=logging.INFO, pathname="p", lineno=1,
        msg="user %s did %s", args=("alice", "login"), exc_info=None,
    )
    fmt = _JsonFormatter()
    out = fmt.format(record)
    parsed = json.loads(out)
    assert parsed["message"] == "user alice did login"


def test_formatter_handles_non_json_serializable_extras():
    fmt = _JsonFormatter()
    # set 같은 비-JSON 타입은 default=str로 string 변환됨
    record = _make_record(custom_obj={1, 2, 3})
    out = fmt.format(record)
    parsed = json.loads(out)
    # set이 string으로 변환되어 들어갔는지
    assert "custom_obj" in parsed


# ─────────────────────────────────────────
# configure_logging — production vs dev 분기
# ─────────────────────────────────────────

def _settings_with_env(env: str):
    class _S:
        ENVIRONMENT = env
    return _S()


def test_configure_logging_production_uses_json_formatter():
    with patch("app.core.logging_config.get_settings", return_value=_settings_with_env("production")):
        configure_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert len(root.handlers) >= 1
    # 적용된 첫 핸들러의 formatter가 _JsonFormatter
    assert isinstance(root.handlers[0].formatter, _JsonFormatter)


def test_configure_logging_development_uses_human_format():
    with patch("app.core.logging_config.get_settings", return_value=_settings_with_env("development")):
        configure_logging()
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    # production이 아니면 _JsonFormatter가 아닌 일반 Formatter
    assert not isinstance(root.handlers[0].formatter, _JsonFormatter)


def test_configure_logging_silences_noisy_loggers():
    with patch("app.core.logging_config.get_settings", return_value=_settings_with_env("development")):
        configure_logging()
    assert logging.getLogger("uvicorn.access").level == logging.WARNING
    assert logging.getLogger("botocore").level == logging.WARNING
    assert logging.getLogger("boto3").level == logging.WARNING
    assert logging.getLogger("pdfminer").level == logging.WARNING

"""Load the Question Quality model from MLflow with champion/challenger canary.

Lecture 13_1 `app/model_loader.py` equivalent. The serving code never hardcodes
a version: it loads the `champion` alias, and (when canary is enabled) routes a
fraction of requests to the `challenger` alias, falling back to champion if no
challenger is set.
"""
from __future__ import annotations

import random

import mlflow
import mlflow.sklearn

from ml.mlops_config import (
    CANARY_ENABLED,
    CANARY_RATIO,
    CHALLENGER_MODEL_URI,
    CHAMPION_MODEL_URI,
    MLFLOW_TRACKING_URI,
)

_cache: dict[str, object] = {}


def _load(uri: str):
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_registry_uri(MLFLOW_TRACKING_URI)
    return mlflow.sklearn.load_model(uri)


def load_champion():
    if "champion" not in _cache:
        _cache["champion"] = _load(CHAMPION_MODEL_URI)
    return _cache["champion"]


def load_challenger():
    if "challenger" not in _cache:
        _cache["challenger"] = _load(CHALLENGER_MODEL_URI)
    return _cache["challenger"]


def select_serving_model() -> tuple[object, str]:
    """Canary: probabilistically pick challenger, else champion.

    Falls back to champion if challenger alias is not set.
    """
    if CANARY_ENABLED and random.random() < CANARY_RATIO:
        try:
            return load_challenger(), "challenger"
        except Exception:
            pass  # no challenger set -> safe fallback
    return load_champion(), "champion"


def reset_cache() -> None:
    """Drop cached models (e.g. after a champion promotion)."""
    _cache.clear()

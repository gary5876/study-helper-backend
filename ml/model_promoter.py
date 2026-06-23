"""Champion promotion logic (lecture 13_1 `ml/model_promoter.py` equivalent).

The serving code always loads `models:/<name>@champion`, so promotion is just
moving the `champion` alias to a better version -- no code change required.
"""
from mlflow.tracking import MlflowClient

from ml.mlops_config import REGISTERED_MODEL_NAME

PROMOTION_METRIC = "test_f1"


def get_champion_metric(client: MlflowClient) -> float:
    """Current champion's promotion metric, or -1.0 if no champion yet."""
    try:
        champion = client.get_model_version_by_alias(
            REGISTERED_MODEL_NAME, "champion"
        )
        run = client.get_run(champion.run_id)
        return run.data.metrics.get(PROMOTION_METRIC, -1.0)
    except Exception:
        return -1.0


def promote_if_better(new_version: str, new_metric: float) -> bool:
    """Move `champion` to new_version iff it beats the current champion.

    Returns True if promotion happened.
    """
    client = MlflowClient()
    current = get_champion_metric(client)

    print(f"[PROMOTION] current champion {PROMOTION_METRIC} = {current}")
    print(f"[PROMOTION] new candidate {PROMOTION_METRIC} = {new_metric}")

    if new_metric > current:
        client.set_registered_model_alias(
            name=REGISTERED_MODEL_NAME,
            alias="champion",
            version=str(new_version),
        )
        print(f"[PROMOTION] version {new_version} promoted to champion")
        return True

    print("[PROMOTION] champion unchanged")
    return False

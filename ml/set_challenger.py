"""Set the `challenger` alias to a non-champion version (lecture RUNBOOK A-6).

Lets the canary route a fraction of requests to a different model than champion.
Run: python -m ml.set_challenger
"""
import mlflow
from mlflow.tracking import MlflowClient

from ml.mlops_config import MLFLOW_TRACKING_URI, REGISTERED_MODEL_NAME


def main() -> None:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_registry_uri(MLFLOW_TRACKING_URI)
    c = MlflowClient()

    champ = c.get_model_version_by_alias(REGISTERED_MODEL_NAME, "champion").version
    versions = sorted(
        int(v.version)
        for v in c.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
    )
    candidates = [v for v in versions if str(v) != str(champ)]
    if not candidates:
        raise SystemExit("No non-champion version to use as challenger. "
                         "Train more versions first.")

    challenger = str(candidates[-1])
    c.set_registered_model_alias(REGISTERED_MODEL_NAME, "challenger", challenger)
    print(f"champion=v{champ}, challenger=v{challenger} set")


if __name__ == "__main__":
    main()

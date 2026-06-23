"""Print the question-quality registry: versions, aliases, metrics.

Run: python -m ml.show_registry
"""
import mlflow
from mlflow.tracking import MlflowClient

from ml.mlops_config import MLFLOW_TRACKING_URI, REGISTERED_MODEL_NAME


def main() -> None:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_registry_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()

    print(f"Registered model: {REGISTERED_MODEL_NAME}")
    # Aliases live on the registered model (alias -> version), not reliably on
    # the ModelVersion returned by search_model_versions.
    rm = client.get_registered_model(REGISTERED_MODEL_NAME)
    version_aliases: dict[str, list[str]] = {}
    for alias, ver in (rm.aliases or {}).items():
        version_aliases.setdefault(str(ver), []).append(alias)

    versions = client.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
    for v in sorted(versions, key=lambda x: int(x.version)):
        run = client.get_run(v.run_id)
        f1 = run.data.metrics.get("test_f1")
        acc = run.data.metrics.get("test_accuracy")
        mtype = run.data.params.get("model_type")
        aliases = ", ".join(version_aliases.get(str(v.version), [])) or "-"
        print(f"  v{v.version:<3} [{aliases:<20}] {mtype:<18} "
              f"f1={f1} acc={acc}")


if __name__ == "__main__":
    main()

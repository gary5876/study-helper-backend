"""MLOps configuration for the Question Quality Classifier (Axis A).

Adapts the lecture's `app/config.py`. Kept separate from the FastAPI app's
`app/core/config.py` so the ML lifecycle runs standalone (no DB / no env file
required). All values are overridable via environment variables.
"""
import os

# --- paths -------------------------------------------------------------------
# ml/ lives directly under the backend repo root.
ML_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(ML_DIR)

DATA_DIR = os.path.join(ML_DIR, "data")
SEED_FILE = os.path.join(DATA_DIR, "seed.jsonl")

# MLflow tracking + registry on a local SQLite store (no server needed).
# SQLite supports the model registry + aliases, so champion/challenger work
# headlessly. Artifacts default to ./mlruns under the backend dir.
_DEFAULT_DB = os.path.join(ML_DIR, "mlflow.db").replace("\\", "/")
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{_DEFAULT_DB}")

EXPERIMENT_NAME = os.getenv("MLOPS_EXPERIMENT", "question-quality-local")
REGISTERED_MODEL_NAME = os.getenv("MLOPS_MODEL_NAME", "question-quality")

# --- serving / canary (lecture 13_1) ----------------------------------------
CHAMPION_MODEL_URI = f"models:/{REGISTERED_MODEL_NAME}@champion"
CHALLENGER_MODEL_URI = f"models:/{REGISTERED_MODEL_NAME}@challenger"

CANARY_ENABLED = os.getenv("CANARY_ENABLED", "true").lower() == "true"
CANARY_RATIO = float(os.getenv("CANARY_RATIO", "0.1"))  # 10% to challenger

# Drop a generated question when its predicted quality score is below this.
# Start low to avoid over-dropping during cold start (see plan §8).
QUALITY_THRESHOLD = float(os.getenv("QUALITY_THRESHOLD", "0.5"))

# --- drift (lecture 13_1) ----------------------------------------------------
LOW_QUALITY_THRESHOLD = float(os.getenv("LOW_QUALITY_THRESHOLD", "0.4"))
LOW_QUALITY_LIMIT = int(os.getenv("LOW_QUALITY_LIMIT", "20"))

# --- determinism -------------------------------------------------------------
RANDOM_STATE = 42

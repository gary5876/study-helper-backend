"""Train the Question Quality Classifier and auto-promote the champion.

Mirrors the lecture's `ml/train.py`: trains 3 candidate models, logs params/
metrics/model to MLflow, registers each, and promotes the best (by macro F1)
to the `champion` alias only if it beats the current champion.

Run:
    python -m ml.build_seed      # produce ml/data/seed.jsonl first
    python -m ml.train
"""
from __future__ import annotations

import json

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.tree import DecisionTreeClassifier

from ml.features import build_feature_frame
from ml.mlops_config import (
    EXPERIMENT_NAME,
    MLFLOW_TRACKING_URI,
    RANDOM_STATE,
    REGISTERED_MODEL_NAME,
    SEED_FILE,
)
from ml.model_promoter import promote_if_better

NUMERIC_COLS = ["q_len", "exp_len", "opt_dup", "level", "is_mcq"]


def load_seed() -> tuple[list[dict], list[str]]:
    questions, labels = [], []
    with open(SEED_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            questions.append(row["q"])
            labels.append(row["label"])
    return questions, labels


def build_pipeline(classifier) -> Pipeline:
    pre = ColumnTransformer(
        transformers=[
            ("tfidf", TfidfVectorizer(min_df=1), "text"),
            ("num", "passthrough", NUMERIC_COLS),
        ]
    )
    return Pipeline([("pre", pre), ("clf", classifier)])


def latest_version(client: MlflowClient) -> str:
    versions = client.search_model_versions(f"name='{REGISTERED_MODEL_NAME}'")
    return max(versions, key=lambda v: int(v.version)).version


def main() -> None:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_registry_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT_NAME)

    questions, labels = load_seed()
    X = build_feature_frame(questions)
    y = labels

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.25, random_state=RANDOM_STATE, stratify=y
    )

    models = {
        "LogisticRegression": LogisticRegression(max_iter=1000,
                                                 random_state=RANDOM_STATE),
        "NaiveBayes": MultinomialNB(),
        "DecisionTree": DecisionTreeClassifier(random_state=RANDOM_STATE),
    }

    client = MlflowClient()
    best_f1 = -1.0
    best_version = None

    for name, clf in models.items():
        with mlflow.start_run(run_name=name):
            pipe = build_pipeline(clf)

            mlflow.log_param("model_type", name)
            mlflow.log_param("vectorizer", "TfidfVectorizer")
            mlflow.log_param("train_rows", len(X_train))
            mlflow.log_param("test_rows", len(X_test))

            pipe.fit(X_train, y_train)
            preds = pipe.predict(X_test)
            acc = accuracy_score(y_test, preds)
            f1 = f1_score(y_test, preds, average="macro")

            mlflow.log_metric("test_accuracy", acc)
            mlflow.log_metric("test_f1", f1)

            mlflow.sklearn.log_model(
                pipe, name="model",
                registered_model_name=REGISTERED_MODEL_NAME,
            )

            version = latest_version(client)
            print(f"[{name}] test_accuracy={acc:.4f} test_f1={f1:.4f} "
                  f"-> registered v{version}")

            if f1 > best_f1:
                best_f1 = f1
                best_version = version

    if best_version is not None:
        promote_if_better(best_version, best_f1)


if __name__ == "__main__":
    main()

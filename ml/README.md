# MLOps — Question Quality Classifier (Axis A)

A real, trained sklearn model with the lecture's full MLOps lifecycle
(train → MLflow registry → champion promotion → champion/challenger canary),
adapted into Study Helper. It scores generated questions `good`/`bad` so the
generation pipeline can drop low-quality items. See the full plan:
`../../documents/plan/2026-06-23-mlops-hybrid-구현-계획.md`.

## Quick start (no Postgres / no LLM needed)

```bash
cd study-helper-backend

python -m ml.build_seed            # 1) make seed dataset (ml/data/seed.jsonl)
python -m ml.train                 # 2) train LR/NB/DT, log to MLflow, promote champion
python -m ml.show_registry         # 3) inspect versions + aliases

python -m ml.build_seed --augment  # 4) add cleaner data...
python -m ml.train                 # 5) ...retrain -> champion auto-swaps if better

python -m ml.set_challenger        # 6) set challenger alias (for canary)
python -m ml.demo_serving          # 7) quality gate + canary demo
```

Optional MLflow UI (the SQLite store is at `ml/mlflow.db`):
```bash
mlflow ui --backend-store-uri sqlite:///ml/mlflow.db --port 5000
# -> http://127.0.0.1:5000  (Models -> question-quality -> @champion alias)
```

## Files

| File | Role | Lecture equiv. |
|---|---|---|
| `mlops_config.py` | tracking URI, model name, canary/threshold settings | `app/config.py` |
| `features.py` | question dict -> text + numeric features (shared train/serve) | — |
| `build_seed.py` | deterministic good/bad seed dataset (`--augment` for retrain demo) | `ml/data/*.csv` |
| `train.py` | train 3 models, log/register to MLflow, promote best by macro-F1 | `ml/train.py` |
| `model_promoter.py` | `promote_if_better` (move `champion` alias) | `ml/model_promoter.py` |
| `set_challenger.py` | set `challenger` alias to a non-champion version | RUNBOOK A-6 |
| `show_registry.py` | list versions/aliases/metrics | `ml/show_registry.py` |
| `demo_serving.py` | standalone gate + canary demo | — |
| `../app/services/model_loader.py` | load champion/challenger + canary select | `app/model_loader.py` |
| `../app/services/quality_gate.py` | `score_and_filter` low-quality questions | — |
| `../tests/test_quality_model.py` | fast tests (features + gate, stub model) | — |

Lecture ingredients covered: ① MLflow tracking/registry · ② training audition ·
③ champion auto-promotion · ④ champion/challenger canary. (Drift→Issue, feedback
DB, dashboard, CI = later phases in the plan.)

## Remaining wire-in (next step, ~10 lines)

In `app/routers/generate.py::_run_generation`, right after
`mcq_list = validate_mcq(...)` / `fill_list = validate_fill(...)`:

```python
from app.services.model_loader import select_serving_model
from app.services.quality_gate import score_and_filter

model, serving_model = select_serving_model()
mcq_gate = score_and_filter(model, mcq_list, serving_model)
fill_gate = score_and_filter(model, fill_list, serving_model)
mcq_list, fill_list = mcq_gate.kept, fill_gate.kept
logger.info("quality gate: dropped %d mcq / %d fill (serving=%s)",
            mcq_gate.dropped_count, fill_gate.dropped_count, serving_model)
```

Not wired yet on purpose: it should be guarded by a config flag + a champion-
exists check, and verified against the full backend deps / running app (out of
the initial 2-hour scope). The gate is fully built, tested, and demoable above.

## Notes
- Determinism: `RANDOM_STATE=42` everywhere; same commands -> same results.
- Cold start: seed labels are synthetic; real labels accrue from user feedback
  (plan §1.2, §3.4) and feed the next retrain — that's the closed MLOps loop.
- `ml/mlflow.db`, `ml/mlruns/`, `ml/data/` are local artifacts — add to
  `.gitignore` before committing.

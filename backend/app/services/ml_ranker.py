"""Train / load / serve the LightGBM setup ranker.

Training data = ml_labels.build_labels over a cached Phase-1 scan (outcome-
labeled setups). Validation is strictly out-of-time (newest ~20% of signal
dates) with an embargo gap so overlapping outcome windows can't leak from
train into validation.

Ship gate: a model only becomes active if it beats the hand-tuned technical
composite baseline on the out-of-time window (AUC and top-decile hit rate).
Metrics for both are stored on the ml_models row either way.

lightgbm / scikit-learn / joblib are OPTIONAL deps (pyproject group [ml]).
All imports are lazy; ml_available() reports whether serving is possible.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import MLModel
from app.services.ml_features import (
    CATEGORICAL_FEATURES,
    FEATURE_NAMES,
    FEATURE_VERSION,
    PATTERN_TYPE_CODES,
    to_matrix,
)
from app.services.ml_labels import LabeledSetup, build_labels
from app.services.signal_scan_service import latest_successful_scan

log = logging.getLogger(__name__)

LABEL_KINDS = ("target_vs_rest", "r_ge_1")
MIN_TRAINING_ROWS = 300

# Mirror of screener_service PATTERN_EV_MULT for the point-in-time baseline
# (full composite isn't reproducible historically — RS/EPS/SMR ranks need
# universe-wide history and fundamentals that don't exist point-in-time).
_BASELINE_EV_MULT = {
    "high_tight_flag": 2.00,
    "ascending_triangle": 1.40,
    "vcp": 1.00,
    "cwh": 1.00,
    "three_weeks_tight": 1.00,
    "bull_flag": 0.85,
    "flat_base": 0.75,
    "none": 0.50,
}
_CODE_TO_PATTERN = {v: k for k, v in PATTERN_TYPE_CODES.items()}

_LGBM_PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "num_leaves": 31,
    "learning_rate": 0.05,
    "min_child_samples": 50,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "seed": 42,
}
_MAX_ROUNDS = 500
_EARLY_STOPPING = 50


def ml_available() -> bool:
    """True when the optional [ml] dependency group is installed."""
    try:
        import joblib  # noqa: F401
        import lightgbm  # noqa: F401
        import sklearn  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class MLTrainProgress:
    """Mutable progress handle owned by the API layer."""
    stage: str = "idle"
    processed: int = 0
    total: int = 0


@dataclass
class LoadedModel:
    id: uuid.UUID
    booster: object
    calibrator: object
    feature_names: list[str]
    feature_version: int
    label_kind: str


def technical_composite_baseline(feats: list[dict]) -> np.ndarray:
    """Point-in-time reconstruction of the composite's technical sub-blend,
    renormalized: (0.12·tt/8 + 0.15·vcp + 0.20·min(1, quality·EV)) / 0.47,
    times the buyability multiplier (at_pivot 1.15 / in_base 1.00)."""
    out = np.zeros(len(feats), dtype=float)
    for idx, f in enumerate(feats):
        tt = float(f.get("tt_score") or 0.0)
        vcp = float(f.get("vcp_score") or 0.0)
        quality = float(f.get("pattern_quality") or 0.0)
        ptype = _CODE_TO_PATTERN.get(f.get("pattern_type_code"), "none")
        ev = _BASELINE_EV_MULT.get(ptype, 0.50)
        buy_mult = 1.15 if f.get("is_at_pivot") else 1.00
        tech = (0.12 * tt / 8.0 + 0.15 * vcp + 0.20 * min(1.0, quality * ev)) / 0.47
        out[idx] = tech * buy_mult
    return out


def _label_vector(rows: list[LabeledSetup], label_kind: str) -> np.ndarray:
    if label_kind == "target_vs_rest":
        return np.array([1 if r.exit_reason == "target" else 0 for r in rows], dtype=int)
    if label_kind == "r_ge_1":
        return np.array([1 if (r.r_multiple or 0.0) >= 1.0 else 0 for r in rows], dtype=int)
    raise ValueError(f"unknown label_kind {label_kind!r}")


def _decile_table(scores: np.ndarray, y: np.ndarray, r: np.ndarray) -> list[dict]:
    """Decile-lift table, decile 1 = highest scores."""
    order = np.argsort(-scores)
    n = len(scores)
    table = []
    for d in range(10):
        lo, hi = (d * n) // 10, ((d + 1) * n) // 10
        idx = order[lo:hi]
        if len(idx) == 0:
            continue
        table.append({
            "decile": d + 1,
            "n": int(len(idx)),
            "mean_score": round(float(np.mean(scores[idx])), 4),
            "hit_rate": round(float(np.mean(y[idx])), 4),
            "mean_r": round(float(np.mean(r[idx])), 4),
        })
    return table


def _calibration_bins(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> list[dict]:
    bins = []
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    for b in range(n_bins):
        mask = (p >= edges[b]) & (p < edges[b + 1] if b < n_bins - 1 else p <= edges[b + 1])
        if not np.any(mask):
            continue
        bins.append({
            "bin_lo": round(float(edges[b]), 2),
            "bin_hi": round(float(edges[b + 1]), 2),
            "mean_pred": round(float(np.mean(p[mask])), 4),
            "frac_pos": round(float(np.mean(y[mask])), 4),
            "n": int(np.sum(mask)),
        })
    return bins


def _artifact_dir() -> Path:
    d = Path(get_settings().ml_artifact_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


async def train_ranker(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID | None = None,
    stop_atr: float = 1.5,
    target_r: float = 2.0,
    time_stop: int = 20,
    trigger_window: int = 30,
    label_kind: str = "auto",
    valid_frac: float = 0.20,
    embargo_bars: int | None = None,
    progress: MLTrainProgress | None = None,
) -> uuid.UUID:
    """Train a ranker on a cached scan's labeled outcomes. Returns ml_models.id.

    label_kind="auto" trains both label variants and keeps the one with the
    better out-of-time top-decile economics (tie-break: AUC).
    """
    import joblib
    import lightgbm as lgb
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss, roc_auc_score

    prog = progress or MLTrainProgress()

    if embargo_bars is None:
        embargo_bars = trigger_window + time_stop

    # Resolve scan: explicit id, else newest successful (any lookback).
    if scan_id is None:
        scan = await latest_successful_scan(session)
        if scan is None:
            raise ValueError("No successful Phase-1 scan cached — run one first.")
        scan_id = scan.id

    model_row = MLModel(
        id=uuid.uuid4(),
        scan_id=scan_id,
        status="training",
        feature_version=FEATURE_VERSION,
        params={
            "stop_atr": stop_atr, "target_r": target_r,
            "time_stop": time_stop, "trigger_window": trigger_window,
            "valid_frac": valid_frac, "embargo_bars": embargo_bars,
            "lgbm": _LGBM_PARAMS, "max_rounds": _MAX_ROUNDS,
            "note": "untriggered candidates excluded (unfilled buy-stop costs nothing); "
                    "P(trigger) companion model is future work",
        },
        feature_names=list(FEATURE_NAMES),
    )
    session.add(model_row)
    await session.commit()
    model_id = model_row.id

    try:
        prog.stage = "labeling"
        labeled = await build_labels(
            session, scan_id=scan_id,
            stop_atr=stop_atr, target_r=target_r,
            time_stop=time_stop, trigger_window=trigger_window,
        )

        n_raw = len(labeled)
        rows = [
            r for r in labeled
            if r.triggered and not r.censored and r.features
            and r.features.get("fv") == FEATURE_VERSION
        ]
        if len(rows) < MIN_TRAINING_ROWS:
            raise ValueError(
                f"Only {len(rows)} usable triggered setups (of {n_raw} candidates) — "
                f"need ≥ {MIN_TRAINING_ROWS}. Run a longer-lookback scan."
            )

        # ── Out-of-time split with embargo ────────────────────────────────────
        prog.stage = "splitting"
        rows.sort(key=lambda r: r.signal_date)
        unique_dates = sorted({r.signal_date for r in rows})
        cutoff_pos = int(len(unique_dates) * (1.0 - valid_frac))
        cutoff_pos = min(max(cutoff_pos, 1), len(unique_dates) - 1)
        cutoff_date = unique_dates[cutoff_pos]
        embargo_pos = max(cutoff_pos - embargo_bars, 0)
        embargo_date = unique_dates[embargo_pos]

        train_rows = [r for r in rows if r.signal_date < embargo_date]
        valid_rows = [r for r in rows if r.signal_date >= cutoff_date]
        if len(train_rows) < MIN_TRAINING_ROWS // 2 or len(valid_rows) < 50:
            raise ValueError(
                f"Split too thin: {len(train_rows)} train / {len(valid_rows)} valid rows."
            )

        x_train = to_matrix([r.features for r in train_rows], FEATURE_NAMES)
        x_valid = to_matrix([r.features for r in valid_rows], FEATURE_NAMES)
        r_valid = np.array([r.r_multiple or 0.0 for r in valid_rows], dtype=float)
        cat_idx = [FEATURE_NAMES.index(c) for c in CATEGORICAL_FEATURES]

        baseline_scores = technical_composite_baseline([r.features for r in valid_rows])

        # ── Train one booster per label kind, keep the better one ────────────
        kinds = list(LABEL_KINDS) if label_kind == "auto" else [label_kind]
        results: dict[str, dict] = {}
        for kind in kinds:
            prog.stage = f"training:{kind}"
            y_train = _label_vector(train_rows, kind)
            y_valid = _label_vector(valid_rows, kind)
            if len(np.unique(y_train)) < 2 or len(np.unique(y_valid)) < 2:
                log.warning("label %s is single-class; skipping", kind)
                continue

            dtrain = lgb.Dataset(
                x_train, label=y_train,
                feature_name=list(FEATURE_NAMES), categorical_feature=cat_idx,
            )
            dvalid = dtrain.create_valid(x_valid, label=y_valid)
            booster = lgb.train(
                _LGBM_PARAMS, dtrain,
                num_boost_round=_MAX_ROUNDS,
                valid_sets=[dvalid],
                callbacks=[
                    lgb.early_stopping(_EARLY_STOPPING, verbose=False),
                    lgb.log_evaluation(0),
                ],
            )
            p_raw = np.asarray(booster.predict(x_valid, num_iteration=booster.best_iteration))
            auc = float(roc_auc_score(y_valid, p_raw))
            decile = _decile_table(p_raw, y_valid, r_valid)
            results[kind] = {
                "booster": booster,
                "y_valid": y_valid,
                "p_raw": p_raw,
                "auc": auc,
                "decile": decile,
                "top_decile_mean_r": decile[0]["mean_r"] if decile else 0.0,
                "top_decile_hit": decile[0]["hit_rate"] if decile else 0.0,
            }

        if not results:
            raise ValueError("No trainable label variant (single-class labels).")

        chosen_kind = max(
            results, key=lambda k: (results[k]["top_decile_mean_r"], results[k]["auc"])
        )
        best = results[chosen_kind]
        booster, y_valid, p_raw = best["booster"], best["y_valid"], best["p_raw"]

        # ── Calibrate on the validation fold ─────────────────────────────────
        prog.stage = "calibrating"
        calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        calibrator.fit(p_raw, y_valid)
        p_cal = np.asarray(calibrator.predict(p_raw))

        # ── Metrics + ship gate vs baseline ──────────────────────────────────
        prog.stage = "evaluating"
        baseline_auc = float(roc_auc_score(y_valid, baseline_scores))
        baseline_decile = _decile_table(baseline_scores, y_valid, r_valid)
        gate = {
            "model_auc": round(best["auc"], 4),
            "baseline_auc": round(baseline_auc, 4),
            "model_top_decile_hit": best["top_decile_hit"],
            "baseline_top_decile_hit": baseline_decile[0]["hit_rate"] if baseline_decile else 0.0,
            "model_top_decile_mean_r": best["top_decile_mean_r"],
            "baseline_top_decile_mean_r": baseline_decile[0]["mean_r"] if baseline_decile else 0.0,
        }
        gate["passed"] = bool(
            gate["model_auc"] > gate["baseline_auc"]
            and gate["model_top_decile_hit"] >= gate["baseline_top_decile_hit"]
        )

        importances = dict(zip(
            FEATURE_NAMES,
            [int(v) for v in booster.feature_importance(importance_type="gain").round()],
            strict=True,
        ))

        metrics = {
            "auc_valid": round(best["auc"], 4),
            "brier_valid": round(float(brier_score_loss(y_valid, p_cal)), 4),
            "pos_rate_train": round(float(np.mean(_label_vector(train_rows, chosen_kind))), 4),
            "pos_rate_valid": round(float(np.mean(y_valid)), 4),
            "calibration_bins": _calibration_bins(p_cal, y_valid),
            "decile_lift": best["decile"],
            "baseline_decile_lift": baseline_decile,
            "ship_gate": gate,
            "label_kind_selection": {
                k: {"auc": round(v["auc"], 4),
                    "top_decile_mean_r": v["top_decile_mean_r"],
                    "top_decile_hit": v["top_decile_hit"]}
                for k, v in results.items()
            },
            "n_candidates_raw": n_raw,
            "n_excluded": n_raw - len(rows),
            "baseline_note": (
                "Baseline is the composite's technical sub-blend "
                "(TT+VCP+pattern, renormalized, × buyability mult) — the full "
                "composite (RS/EPS/SMR ranks) is not reproducible point-in-time."
            ),
            "survivorship_note": (
                "Universe = today's active symbols; delisted losers absent, so "
                "probabilities read slightly high. Use for ranking."
            ),
        }

        # ── Persist artifact + model row ──────────────────────────────────────
        prog.stage = "saving"
        artifact_path = _artifact_dir() / f"ranker_{model_id}.joblib"
        joblib.dump(
            {
                "booster": booster,
                "calibrator": calibrator,
                "feature_names": list(FEATURE_NAMES),
                "feature_version": FEATURE_VERSION,
                "label_kind": chosen_kind,
                "params": model_row.params,
            },
            artifact_path,
        )

        if gate["passed"]:
            await session.execute(
                update(MLModel).where(MLModel.is_active == True).values(is_active=False)  # noqa: E712
            )
        else:
            log.warning(
                "model %s FAILED the ship gate (auc %.3f vs baseline %.3f) — "
                "saved but not activated", model_id, gate["model_auc"], gate["baseline_auc"],
            )

        model_row.status = "success"
        model_row.label_kind = chosen_kind
        model_row.metrics = metrics
        model_row.feature_importances = importances
        model_row.n_train = len(train_rows)
        model_row.n_valid = len(valid_rows)
        model_row.train_end_date = datetime.fromisoformat(embargo_date)
        model_row.valid_start_date = datetime.fromisoformat(cutoff_date)
        model_row.artifact_path = str(artifact_path)
        model_row.is_active = gate["passed"]
        await session.commit()

        prog.stage = "done"
        log.info(
            "trained ranker %s: kind=%s auc=%.3f (baseline %.3f) gate=%s "
            "n_train=%d n_valid=%d",
            model_id, chosen_kind, gate["model_auc"], gate["baseline_auc"],
            gate["passed"], len(train_rows), len(valid_rows),
        )
        return model_id

    except Exception as exc:
        model_row.status = "failed"
        model_row.error = str(exc)[:480]
        await session.commit()
        raise


# ─── Serving ──────────────────────────────────────────────────────────────────

_loaded_cache: dict[uuid.UUID, LoadedModel] = {}


async def load_active_model(session: AsyncSession) -> LoadedModel | None:
    """Load the active model's artifact (cached per process by model id)."""
    if not ml_available():
        return None
    q = await session.execute(
        select(MLModel)
        .where(MLModel.is_active == True, MLModel.status == "success")  # noqa: E712
        .order_by(MLModel.created_at.desc())
        .limit(1)
    )
    row = q.scalars().first()
    if row is None or not row.artifact_path:
        return None
    cached = _loaded_cache.get(row.id)
    if cached is not None:
        return cached

    import joblib
    try:
        blob = joblib.load(row.artifact_path)
    except FileNotFoundError:
        log.warning("active model %s artifact missing at %s", row.id, row.artifact_path)
        return None
    model = LoadedModel(
        id=row.id,
        booster=blob["booster"],
        calibrator=blob["calibrator"],
        feature_names=blob["feature_names"],
        feature_version=blob["feature_version"],
        label_kind=blob["label_kind"],
    )
    _loaded_cache.clear()   # only ever one active model per process
    _loaded_cache[row.id] = model
    return model


def predict_proba(model: LoadedModel, feats: list[dict]) -> list[float]:
    """Calibrated P(win) for a batch of feature dicts."""
    if not feats:
        return []
    x = to_matrix(feats, model.feature_names)
    p_raw = np.asarray(model.booster.predict(x))
    p_cal = np.asarray(model.calibrator.predict(p_raw))
    return [float(np.clip(p, 0.0, 1.0)) for p in p_cal]

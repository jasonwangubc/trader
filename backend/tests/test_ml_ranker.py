"""Ranker training smoke test on synthetic labeled setups.

build_labels is stubbed (its own correctness is pinned in test_ml_labels.py);
here we verify the training pipeline: out-of-time split with embargo, LightGBM
fit, isotonic calibration, ship gate vs baseline, artifact write, DB row, and
the load/predict serving path.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("lightgbm")
pytest.importorskip("sklearn")

import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.models import BacktestSignalScan, MLModel
from app.services.ml_features import FEATURE_VERSION
from app.services.ml_labels import LabeledSetup
from app.services.ml_ranker import (
    load_active_model,
    predict_proba,
    train_ranker,
)

N_DATES = 300
ROWS_PER_DATE = 2


@pytest.fixture
def artifact_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ML_ARTIFACT_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _feats(signal_strength: float) -> dict:
    """Minimal feature dict; ret_21d carries the (deterministic) signal.
    Baseline inputs (tt/vcp/quality) are constant → baseline AUC = 0.5."""
    return {
        "fv": FEATURE_VERSION,
        "ret_21d": signal_strength,
        "tt_score": 6,
        "vcp_score": 0.7,
        "pattern_quality": 0.7,
        "pattern_type_code": 0,
        "is_at_pivot": 1,
    }


def _synthetic_labels() -> list[LabeledSetup]:
    rows = []
    start = datetime(2024, 1, 1)
    for d in range(N_DATES):
        date_str = (start + timedelta(days=d)).strftime("%Y-%m-%d")
        for j in range(ROWS_PER_DATE):
            # Alternate winners and losers; ret_21d fully determines the outcome.
            win = (d + j) % 2 == 0
            signal = 10.0 + d * 0.01 if win else -10.0 - d * 0.01
            rows.append(LabeledSetup(
                candidate_id=uuid.uuid4(),
                symbol=f"SYN{j}",
                signal_date=date_str,
                features=_feats(signal),
                triggered=True,
                censored=False,
                days_to_trigger=1,
                exit_reason="target" if win else "stop",
                r_multiple=2.0 if win else -1.0,
                mae_r=-0.2 if win else -1.0,
                mfe_r=2.2 if win else 0.3,
                mae_pct=-1.0,
                mfe_pct=11.0,
                fwd_ret_5=5.0 if win else -3.0,
                fwd_ret_10=8.0 if win else -4.0,
                fwd_ret_20=11.0 if win else -5.0,
            ))
    return rows


async def test_train_ranker_end_to_end(db_session, artifact_dir, monkeypatch):
    scan = BacktestSignalScan(
        lookback_days=1260, symbols_scanned=2, candidate_count=600,
        status="success", finished_at=datetime(2025, 1, 1),
    )
    db_session.add(scan)
    await db_session.commit()

    labels = _synthetic_labels()

    async def fake_build_labels(session, **kwargs):
        return labels

    monkeypatch.setattr("app.services.ml_ranker.build_labels", fake_build_labels)

    model_id = await train_ranker(
        db_session, scan_id=scan.id, label_kind="target_vs_rest",
    )

    row = (
        await db_session.execute(select(MLModel).where(MLModel.id == model_id))
    ).scalar_one()
    assert row.status == "success"
    assert row.label_kind == "target_vs_rest"

    # Out-of-time split with embargo: 300 unique dates, valid_frac 0.20 →
    # cutoff at date index 240; embargo 50 → train dates are indexes 0..189.
    assert row.n_valid == 60 * ROWS_PER_DATE
    assert row.n_train == 190 * ROWS_PER_DATE
    assert row.train_end_date < row.valid_start_date

    # A perfectly learnable signal must produce near-perfect OOT metrics and
    # crush the constant baseline → gate passes, model activates.
    metrics = row.metrics
    assert metrics["auc_valid"] > 0.9
    assert metrics["ship_gate"]["passed"] is True
    assert metrics["ship_gate"]["baseline_auc"] == pytest.approx(0.5, abs=0.05)
    assert row.is_active

    assert row.artifact_path and artifact_dir in __import__("pathlib").Path(row.artifact_path).parents
    assert __import__("pathlib").Path(row.artifact_path).exists()

    # Feature importance should be dominated by the signal feature.
    top_feature = max(row.feature_importances, key=row.feature_importances.get)
    assert top_feature == "ret_21d"

    # Serving path: load the active model and predict on fresh feature dicts.
    model = await load_active_model(db_session)
    assert model is not None and model.id == model_id
    p_win, p_lose = predict_proba(model, [_feats(12.0), _feats(-12.0)])
    assert 0.0 <= p_lose < p_win <= 1.0
    assert p_win > 0.7 and p_lose < 0.3


async def test_train_ranker_fails_cleanly_on_thin_data(db_session, artifact_dir, monkeypatch):
    scan = BacktestSignalScan(
        lookback_days=504, symbols_scanned=1, candidate_count=10,
        status="success", finished_at=datetime(2025, 1, 1),
    )
    db_session.add(scan)
    await db_session.commit()

    async def fake_build_labels(session, **kwargs):
        return _synthetic_labels()[:20]

    monkeypatch.setattr("app.services.ml_ranker.build_labels", fake_build_labels)

    with pytest.raises(ValueError, match="usable triggered setups"):
        await train_ranker(db_session, scan_id=scan.id)

    row = (
        await db_session.execute(
            select(MLModel).where(MLModel.scan_id == scan.id)
        )
    ).scalar_one()
    assert row.status == "failed"
    assert "usable triggered setups" in (row.error or "")
    assert not row.is_active

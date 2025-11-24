"""
Tests for Conformal Prediction modules.

Validates the logic for Residual, CQR, and Adaptive conformal predictors,
ensuring coverage guarantees and correct interval calculations.
"""

import numpy as np
import pandas as pd
import pytest

from nf_loto_platform.ml.conformal import (
    ResidualConformalPredictor,
    CQRConformalPredictor,
    AdaptiveConformalPredictor,
    get_conformal_predictor,
    PredictionIntervals
)


# --- Fixtures ---

@pytest.fixture
def mock_point_data():
    """Simple synthetic data for point prediction testing."""
    # y_true: [10, 12, 11, 13, 10]
    # y_pred: [10.5, 11.5, 11.0, 12.5, 10.5]
    # errors: [0.5, 0.5, 0.0, 0.5, 0.5]
    y_true = np.array([10.0, 12.0, 11.0, 13.0, 10.0])
    y_pred = np.array([10.5, 11.5, 11.0, 12.5, 10.5])
    return y_true, y_pred


@pytest.fixture
def mock_interval_data():
    """Synthetic data for quantile/interval prediction testing."""
    y_true = np.array([10.0, 12.0, 11.0])
    # Model predicts intervals that are slightly too narrow
    y_lower = np.array([9.5, 11.8, 10.8]) # barely misses some
    y_upper = np.array([10.5, 12.2, 11.2])
    y_hat = (y_lower + y_upper) / 2
    return y_true, y_hat, y_lower, y_upper


# --- Residual CP Tests ---

def test_residual_cp_calibration_and_prediction(mock_point_data):
    y_true, y_pred = mock_point_data
    # alpha=0.2 -> 80% confidence
    cp = ResidualConformalPredictor(alpha=0.2)
    
    # 1. Calibrate
    cp.calibrate(y_true, y_pred)
    assert cp.q_val is not None
    assert cp.q_val >= 0.5 # Based on mock data errors
    
    # 2. Predict
    y_test_pred = np.array([11.0, 12.0])
    intervals = cp.predict(y_test_pred)
    
    assert isinstance(intervals, PredictionIntervals)
    assert intervals.method == "ResidualCP"
    assert intervals.confidence_level == 0.8
    
    # Residual CP produces fixed width intervals
    widths = intervals.y_upper - intervals.y_lower
    assert np.allclose(widths[0], widths[1])
    assert np.allclose(widths[0], 2 * cp.q_val)
    
    # Check bounds
    np.testing.assert_allclose(intervals.y_lower, y_test_pred - cp.q_val)
    np.testing.assert_allclose(intervals.y_upper, y_test_pred + cp.q_val)


def test_residual_cp_uncalibrated_error(mock_point_data):
    _, y_pred = mock_point_data
    cp = ResidualConformalPredictor()
    with pytest.raises(RuntimeError, match="not calibrated"):
        cp.predict(y_pred)


# --- CQR Tests ---

def test_cqr_cp_calibration_and_prediction(mock_interval_data):
    y_true, y_hat, y_lower, y_upper = mock_interval_data
    
    cp = CQRConformalPredictor(alpha=0.2)
    
    # 1. Calibrate (Input is tuple of lower, upper)
    cp.calibrate(y_true, (y_lower, y_upper))
    assert cp.q_val is not None
    # With current mock data, q_val should be positive to correct coverage
    
    # 2. Predict (Input is tuple of hat, lower, upper)
    y_test_hat = np.array([15.0])
    y_test_lower = np.array([14.0])
    y_test_upper = np.array([16.0])
    
    intervals = cp.predict((y_test_hat, y_test_lower, y_test_upper))
    
    assert intervals.method == "CQR"
    
    # CQR adjusts existing intervals by q_val
    expected_lower = y_test_lower - cp.q_val
    expected_upper = y_test_upper + cp.q_val
    
    np.testing.assert_allclose(intervals.y_lower, expected_lower)
    np.testing.assert_allclose(intervals.y_upper, expected_upper)


# --- Adaptive CP Tests ---

def test_adaptive_cp_calibration_and_prediction(mock_point_data):
    y_true, y_pred = mock_point_data
    # Mock sigma (volatility) estimates for each point
    sigma_hat = np.array([1.0, 0.5, 1.0, 0.5, 1.0])
    
    cp = AdaptiveConformalPredictor(alpha=0.2)
    
    # 1. Calibrate with sigma
    cp.calibrate(y_true, y_pred, sigma_hat=sigma_hat)
    assert cp.q_val is not None
    
    # 2. Predict
    y_test_pred = np.array([11.0, 12.0])
    y_test_sigma = np.array([2.0, 0.5]) # High vol vs Low vol
    
    intervals = cp.predict(y_test_pred, sigma_hat=y_test_sigma)
    
    assert intervals.method == "AdaptiveCP"
    
    # Interval widths should depend on sigma
    width_1 = intervals.y_upper[0] - intervals.y_lower[0]
    width_2 = intervals.y_upper[1] - intervals.y_lower[1]
    
    # width = 2 * q_val * sigma
    # So width_1 (sigma=2.0) should be 4x larger than width_2 (sigma=0.5)
    assert width_1 > width_2
    np.testing.assert_allclose(width_1 / 2.0, width_2 / 0.5)


# --- Factory & Utils Tests ---

def test_get_conformal_predictor_factory():
    cp1 = get_conformal_predictor("residual", alpha=0.1)
    assert isinstance(cp1, ResidualConformalPredictor)
    assert cp1.alpha == 0.1
    
    cp2 = get_conformal_predictor("cqr", alpha=0.05)
    assert isinstance(cp2, CQRConformalPredictor)
    assert cp2.alpha == 0.05
    
    cp3 = get_conformal_predictor("adaptive")
    assert isinstance(cp3, AdaptiveConformalPredictor)
    
    with pytest.raises(ValueError, match="Unknown conformal method"):
        get_conformal_predictor("unknown_method")


def test_prediction_intervals_to_dataframe():
    intervals = PredictionIntervals(
        y_hat=np.array([10.0, 11.0]),
        y_lower=np.array([9.0, 10.0]),
        y_upper=np.array([11.0, 12.0]),
        method="test_method",
        confidence_level=0.9,
        q_val=1.5
    )
    
    unique_ids = np.array(["S1", "S1"])
    ds = np.array(["2023-01-01", "2023-01-02"])
    
    df = intervals.to_dataframe(unique_ids=unique_ids, ds=ds)
    
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "unique_id" in df.columns
    assert "ds" in df.columns
    assert "y_hat" in df.columns
    assert "y_lower" in df.columns
    assert "y_upper" in df.columns
    assert df.iloc[0]["unique_id"] == "S1"
    assert df.iloc[0]["y_lower"] == 9.0


def test_evaluate_coverage():
    # Ground truth
    y_true = np.array([10, 10, 10, 10])
    # Intervals
    y_lower = np.array([9, 9, 11, 9])  # 3rd one (11) > y_true (10), so miss
    y_upper = np.array([11, 11, 12, 8]) # 4th one (8) < y_true (10), so miss
    
    # Should cover indices 0 and 1 -> 50% coverage
    
    stats = ResidualConformalPredictor.evaluate_coverage(y_true, y_lower, y_upper)
    
    assert stats["coverage_rate"] == 0.5
    assert stats["covered_count"] == 2
    assert stats["total_count"] == 4
    assert stats["mean_width"] > 0
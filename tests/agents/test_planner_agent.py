"""
Tests for PlannerAgent.

タスク仕様 (TimeSeriesTaskSpec) とキュレーターの出力 (CuratorOutput) に基づき、
適切な実験レシピ (ExperimentRecipe) が生成されるかをテストする。
"""

from typing import Dict, Any
from unittest.mock import MagicMock
import pytest

from nf_loto_platform.agents.planner_agent import PlannerAgent
from nf_loto_platform.agents.domain import TimeSeriesTaskSpec, CuratorOutput
from nf_loto_platform.ml.model_registry import AutoModelSpec

# --- Fixtures ---

@pytest.fixture
def mock_registry():
    """テスト用の簡易モデルレジストリ. ヒューリスティックロジックが反応する名前を含める."""
    return {
        # Standard NeuralForecast model
        "ModelA_NF": AutoModelSpec(
            name="ModelA_NF", family="MLP", univariate=True, multivariate=False,
            forecast_type="direct", exogenous=MagicMock(), engine_kind="neuralforecast"
        ),
        # TSFM (Foundation Model)
        "ModelB_TSFM": AutoModelSpec(
            name="ModelB_TSFM", family="Transformer", univariate=True, multivariate=True,
            forecast_type="recursive", exogenous=MagicMock(), engine_kind="tsfm"
        ),
        # Classical model
        "ModelC_Classic": AutoModelSpec(
            name="ModelC_Classic", family="Statistical", univariate=True, multivariate=False,
            forecast_type="recursive", exogenous=MagicMock(), engine_kind="classical"
        ),
        # Specific models for heuristics testing
        "AutoNHITS": AutoModelSpec(
            name="AutoNHITS", family="NHITS", univariate=True, multivariate=False,
            forecast_type="direct", exogenous=MagicMock(), engine_kind="neuralforecast"
        ),
        "AutoDLinear": AutoModelSpec(
            name="AutoDLinear", family="DLinear", univariate=True, multivariate=False,
            forecast_type="direct", exogenous=MagicMock(), engine_kind="neuralforecast"
        ),
        # Disabled model
        "ModelD_Disabled": AutoModelSpec(
            name="ModelD_Disabled", family="MLP", univariate=True, multivariate=False,
            forecast_type="direct", exogenous=MagicMock(), engine_kind="neuralforecast",
            enabled=False # Explicitly disabled
        )
    }

@pytest.fixture
def simple_task():
    return TimeSeriesTaskSpec(
        name="test_task",
        table_name="test_table",
        target_column="y",
        target_horizon=24,
        max_training_time_minutes=120, # 2 hours
        allow_neuralforecast=True,
        allow_tsfm=True,
        allow_classical=True
    )

def create_mock_curator_output(properties: Dict[str, Any]) -> CuratorOutput:
    """データ特性を指定して CuratorOutput を作成するヘルパー."""
    mock_output = MagicMock(spec=CuratorOutput)
    mock_output.summary = "Test summary"
    mock_output.candidate_feature_sets = ["basic_features"]
    mock_output.recommended_validation_scheme = "cv"
    mock_output.dataset_properties = properties
    return mock_output

@pytest.fixture
def curator_output_generic():
    return create_mock_curator_output({"n_obs": 2000, "seasonality_strength": 0.1, "trend_strength": 0.1})

# --- Tests ---

def test_select_models_filtering(mock_registry, simple_task):
    """allow_xxx フラグに基づいてモデルがフィルタリングされるか確認."""
    planner = PlannerAgent(registry=mock_registry)
    
    # Case 1: All allowed
    candidates = planner._get_allowed_candidates(simple_task)
    assert "ModelA_NF" in candidates
    assert "ModelB_TSFM" in candidates
    assert "ModelC_Classic" in candidates
    assert "ModelD_Disabled" not in candidates # enabled=False should be filtered

    # Case 2: Only TSFM allowed
    simple_task.allow_neuralforecast = False
    simple_task.allow_classical = False
    candidates_tsfm = planner._get_allowed_candidates(simple_task)
    assert "ModelA_NF" not in candidates_tsfm
    assert "ModelB_TSFM" in candidates_tsfm
    assert "ModelC_Classic" not in candidates_tsfm


def test_plan_creation_backend_selection(mock_registry, simple_task, curator_output_generic):
    """トレーニング時間に応じてバックエンドが適切に選択されるか確認."""
    planner = PlannerAgent(registry=mock_registry)
    
    # Case 1: Long training time -> optuna
    simple_task.max_training_time_minutes = 120
    recipe_long = planner.plan(simple_task, curator_output_generic)
    assert recipe_long.search_backend == "optuna"
    assert recipe_long.time_budget_hours == 2.0
    
    # Case 2: Short training time -> local
    simple_task.max_training_time_minutes = 30
    recipe_short = planner.plan(simple_task, curator_output_generic)
    assert recipe_short.search_backend == "local"
    assert recipe_short.time_budget_hours == 0.5


def test_dynamic_scoring_small_data(mock_registry, simple_task):
    """データ量が少ない場合、TSFMやClassicalが優先されるかテスト."""
    planner = PlannerAgent(registry=mock_registry)
    
    # n_obs < 1000 -> TSFM should get +2.0 score
    small_data_curator = create_mock_curator_output({
        "n_obs": 500, 
        "seasonality_strength": 0.1, 
        "trend_strength": 0.1
    })
    
    recipe = planner.plan(simple_task, small_data_curator)
    ranked_models = recipe.models
    
    # TSFMモデルが上位に来ることを期待
    assert ranked_models[0] == "ModelB_TSFM"
    # NeuralForecast系はデータ不足のためスコアが下がるはず
    assert ranked_models[-1] in ["ModelA_NF", "AutoNHITS", "AutoDLinear"]


def test_dynamic_scoring_high_seasonality(mock_registry, simple_task):
    """季節性が強い場合、NHITSなどが優先されるかテスト."""
    planner = PlannerAgent(registry=mock_registry)
    
    # seasonality_strength > 0.6 -> NHITS should get +1.5 score
    seasonal_curator = create_mock_curator_output({
        "n_obs": 2000, # 十分なデータ量
        "seasonality_strength": 0.9, 
        "trend_strength": 0.1
    })
    
    recipe = planner.plan(simple_task, seasonal_curator)
    ranked_models = recipe.models
    
    # AutoNHITS が上位に来ることを期待
    # (スコア計算: base=1.0, high_seasonality=+1.5 => 2.5)
    assert ranked_models[0] == "AutoNHITS" 


def test_dynamic_scoring_high_trend(mock_registry, simple_task):
    """トレンドが強い場合、DLinearが優先されるかテスト."""
    planner = PlannerAgent(registry=mock_registry)
    
    # trend_strength > 0.6 -> DLinear should get +1.5 score
    trend_curator = create_mock_curator_output({
        "n_obs": 2000, 
        "seasonality_strength": 0.1, 
        "trend_strength": 0.9
    })
    
    recipe = planner.plan(simple_task, trend_curator)
    ranked_models = recipe.models
    
    # AutoDLinear が上位に来ることを期待
    assert ranked_models[0] == "AutoDLinear"


def test_plan_includes_curator_recommendations(mock_registry, simple_task, curator_output_generic):
    """Curatorからの特徴量候補などがレシピに含まれるか確認."""
    planner = PlannerAgent(registry=mock_registry)
    recipe = planner.plan(simple_task, curator_output_generic)
    
    assert recipe.feature_sets == ["basic_features"]
    assert recipe.extra_params["validation_scheme"] == "cv"
    assert recipe.extra_params["target_horizon"] == 24
    assert recipe.extra_params["planning_strategy"] == "dynamic_heuristics"
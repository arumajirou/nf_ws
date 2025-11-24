"""
Tests for ReflectionAgent.

実験結果(ExperimentResult)を受け取り、メトリクスや設定に基づいて
適切な批評(Critique)と終了判定(is_satisfied)を行えるかテストする。
"""

from unittest.mock import MagicMock, patch
import pytest
from nf_loto_platform.agents.reflection_agent import ReflectionAgent
from nf_loto_platform.ml.model_runner import ExperimentResult

@pytest.fixture
def reflection_agent():
    config = {
        "agents": {"reflection": {"name": "TestReflector"}},
        "llm": {}
    }
    # LLMClientをモック化して初期化
    with patch("nf_loto_platform.agents.reflection_agent.LLMClient"):
        return ReflectionAgent(config)

def test_diagnose_overfitting(reflection_agent):
    """過学習（Train Loss << Val Loss）を検出できるか."""
    metrics = {"train_loss": 0.1, "val_loss": 0.5, "mae": 0.4} # Val is 5x Train
    uncertainty = {}
    
    issues = reflection_agent._diagnose_issues(metrics, uncertainty)
    
    assert any("Overfitting" in issue for issue in issues)

def test_diagnose_under_confidence(reflection_agent):
    """カバレッジ不足（実際のカバー率 < 目標）を検出できるか."""
    metrics = {"mae": 0.1}
    uncertainty = {"coverage_rate": 0.5} # Target typically 0.9
    
    issues = reflection_agent._diagnose_issues(metrics, uncertainty)
    
    assert any("Under-confident" in issue for issue in issues)

def test_evaluate_success_logic(reflection_agent):
    """LLMの応答に基づいて終了判定ができるか."""
    
    # Mock result
    result = MagicMock(spec=ExperimentResult)
    result.meta = {
        "metrics": {"mae": 0.01}, # Very good score
        "best_params": {},
        "model_name": "TestModel"
    }
    
    # Mock LLM response
    reflection_agent.llm.chat_completion.return_value = "The performance is excellent. I am SATISFIED."
    
    critique, satisfied = reflection_agent.evaluate(result, "mae", [])
    
    assert satisfied is True
    assert "SATISFIED" in critique

def test_evaluate_failure_logic(reflection_agent):
    """改善が必要な場合の判定."""
    
    result = MagicMock(spec=ExperimentResult)
    result.meta = {
        "metrics": {"mae": 100.0}, # Bad score
        "best_params": {},
        "model_name": "TestModel"
    }
    
    reflection_agent.llm.chat_completion.return_value = "The error is too high. Try increasing epochs."
    
    critique, satisfied = reflection_agent.evaluate(result, "mae", [])
    
    assert satisfied is False
    assert "Try increasing epochs" in critique
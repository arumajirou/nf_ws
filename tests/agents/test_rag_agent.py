"""
Tests for RagAgent.

類似パターン検索ロジック、および検索結果の要約機能（トレンド判定など）をテストする。
"""

from unittest.mock import MagicMock, patch
import pandas as pd
import pytest

from nf_loto_platform.agents.rag_agent import RagAgent

@pytest.fixture
def mock_config():
    return {
        "agents": {
            "rag": {
                "name": "TestRAG",
                "config": {"top_k": 3, "similarity_threshold": 0.8}
            }
        },
        "llm": {} # Mocked anyway
    }

@pytest.fixture
def sample_panel_data():
    # 直近データとして使用
    return pd.DataFrame({
        "unique_id": ["S1"] * 20,
        "ds": pd.date_range("2024-01-01", periods=20),
        "y": range(20)
    })

@pytest.fixture
def mock_search_result():
    # search_similar_patterns の戻り値 (DataFrame)
    return pd.DataFrame([
        {"ds": "2023-01-01", "similarity": 0.95, "next_value": 100.0}, # Up
        {"ds": "2022-01-01", "similarity": 0.90, "next_value": 105.0}, # Up
        {"ds": "2021-01-01", "similarity": 0.85, "next_value": 5.0}    # Down
    ])

@patch("nf_loto_platform.agents.rag_agent.LLMClient")
@patch("nf_loto_platform.agents.rag_agent.load_panel_by_loto")
@patch("nf_loto_platform.agents.rag_agent.search_similar_patterns")
def test_search_workflow(
    mock_search_patterns, 
    mock_load_panel, 
    mock_llm_cls, 
    mock_config, 
    sample_panel_data, 
    mock_search_result
):
    agent = RagAgent(mock_config)
    
    # Setup Mocks
    mock_load_panel.return_value = sample_panel_data
    mock_search_patterns.return_value = mock_search_result
    
    # Execute Search
    # 直近のデータ(20)の最後が '19'. 
    # 検索結果の next_value は 100, 105 (Up), 5 (Down)
    result = agent.search("test_table", "loto6", ["S1"], horizon=5)
    
    # Assertions
    assert "details" in result
    assert "S1" in result["details"]
    
    s1_result = result["details"]["S1"]
    
    # 傾向分析のテスト: 2つがUp(>19), 1つがDown(<19) -> 66% Up -> Bullish
    assert len(s1_result["matches"]) == 3
    assert s1_result["up_probability"] > 0.6
    assert s1_result["trend_hint"] == "Bullish"
    
    # API呼び出しチェック
    mock_load_panel.assert_called_once()
    mock_search_patterns.assert_called_once()

def test_empty_data_handling(mock_config):
    """データが存在しない場合のハンドリング."""
    agent = RagAgent(mock_config)
    with patch("nf_loto_platform.agents.rag_agent.load_panel_by_loto") as mock_load:
        mock_load.return_value = pd.DataFrame()
        result = agent.search("t", "l", ["S1"], 5)
        assert "error" in result
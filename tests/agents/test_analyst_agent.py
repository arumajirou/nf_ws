"""
Tests for the AnalystAgent.

Tests the statistical analysis logic, prompt construction, and interaction with the LLM client.
External dependencies (DB, LLM API) are mocked.
"""

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from nf_loto_platform.agents.analyst_agent import AnalystAgent


# --- Fixtures ---

@pytest.fixture
def mock_config():
    """Mock configuration for the agent."""
    return {
        "llm": {
            "default_provider": "openai",
            "default_model": "gpt-4o"
        },
        "agents": {
            "analyst": {
                "name": "TestAnalyst",
                "system_prompt": "Test System Prompt"
            }
        }
    }


@pytest.fixture
def sample_panel_df():
    """Create a sample panel DataFrame for testing."""
    # Create a clear upward trend with seasonality
    dates = pd.date_range(start="2023-01-01", periods=100, freq="D")
    y_values = np.linspace(0, 10, 100) + np.sin(np.linspace(0, 20, 100))
    
    # Add some outliers
    y_values[50] = 100.0  # Extreme outlier
    
    df = pd.DataFrame({
        "unique_id": ["S1"] * 100,
        "ds": dates,
        "y": y_values
    })
    return df


@pytest.fixture
def mock_llm_client():
    """Mock the LLMClient to avoid real API calls."""
    with patch("nf_loto_platform.agents.analyst_agent.LLMClient") as MockClient:
        client_instance = MockClient.return_value
        client_instance.chat_completion.return_value = "Analysis Report: The data shows an upward trend."
        yield client_instance


@pytest.fixture
def analyst_agent(mock_config, mock_llm_client):
    """Initialize an AnalystAgent with mocked dependencies."""
    return AnalystAgent(mock_config)


# --- Tests ---

def test_initialization(analyst_agent, mock_config):
    """Test that the agent is initialized correctly."""
    assert analyst_agent.name == "TestAnalyst"
    assert analyst_agent.system_prompt == "Test System Prompt"
    assert analyst_agent.llm is not None


def test_compute_statistics(analyst_agent, sample_panel_df):
    """Test the statistical calculation logic."""
    stats = analyst_agent._compute_statistics(sample_panel_df)
    
    # Check top-level structure
    assert "global_stats" in stats
    assert "series_analysis" in stats
    assert stats["n_series"] == 1
    assert stats["n_obs"] == 100
    
    # Check specific series stats
    s1_stats = stats["series_analysis"]["S1"]
    assert s1_stats["length"] == 100
    assert s1_stats["outlier_count"] == 1  # We added 1 obvious outlier
    
    # Trend detection (Should be upward because of linspace(0, 10))
    assert s1_stats["trend"] == "Upward"


def test_analyze_flow(analyst_agent, sample_panel_df, mock_llm_client):
    """Test the full analyze flow with mocked DB load."""
    
    # Mock the DB loader
    with patch("nf_loto_platform.agents.analyst_agent.load_panel_by_loto") as mock_load:
        mock_load.return_value = sample_panel_df
        
        # Execute analysis
        report = analyst_agent.analyze(
            table_name="nf_loto_test",
            loto="loto6",
            unique_ids=["S1"]
        )
        
        # Verify DB was called
        mock_load.assert_called_once_with("nf_loto_test", "loto6", ["S1"])
        
        # Verify LLM was called
        mock_llm_client.chat_completion.assert_called_once()
        
        # Verify prompt contains statistical info
        call_args = mock_llm_client.chat_completion.call_args
        user_prompt = call_args.kwargs["user_prompt"]
        assert "Computed Statistics (JSON)" in user_prompt
        assert '"trend": "Upward"' in user_prompt  # Should be in the JSON part
        
        # Verify return value
        assert report == "Analysis Report: The data shows an upward trend."


def test_analyze_empty_data(analyst_agent):
    """Test handling of empty data."""
    
    with patch("nf_loto_platform.agents.analyst_agent.load_panel_by_loto") as mock_load:
        mock_load.return_value = pd.DataFrame()  # Empty DF
        
        report = analyst_agent.analyze(
            table_name="nf_loto_empty",
            loto="loto6",
            unique_ids=["S1"]
        )
        
        assert "No data found" in report


def test_build_analysis_prompt(analyst_agent):
    """Test that the prompt is formatted correctly."""
    summary = {
        "n_series": 1,
        "series_analysis": {
            "S1": {"mean": 10.5, "trend": "Flat"}
        }
    }
    
    prompt = analyst_agent._build_analysis_prompt("loto7", ["S1"], summary)
    
    assert "Loto Type: loto7" in prompt
    assert "Series IDs: ['S1']" in prompt
    assert '"mean": 10.5' in prompt
    assert '"trend": "Flat"' in prompt
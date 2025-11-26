import sys
from unittest.mock import patch, MagicMock
import pytest
from nf_loto_platform.pipelines.easytsf_runner import main

def test_easytsf_runner_cli_success(tmp_path):
    """Test successful CLI execution with mocked dependencies."""
    # Create a dummy config file
    config_file = tmp_path / "test_config.json"
    config_file.write_text('{"dataset": {"unique_ids": ["S1"]}, "experiment": {"horizon": 12}}')
    
    # Mock run_easytsf to avoid actual execution
    with patch("nf_loto_platform.pipelines.easytsf_runner.run_easytsf") as mock_run:
        # Mock return values
        mock_outcome = MagicMock()
        mock_outcome.best_model_name = "TestModel"
        mock_outcome.run_ids = ["123"]
        
        mock_report = MagicMock()
        mock_report.conclusion = "Success"
        
        mock_meta = {}
        
        mock_run.return_value = (mock_outcome, mock_report, mock_meta)
        
        # Mock sys.argv
        test_args = ["easytsf_runner.py", "--config", str(config_file), "--tag", "test_run"]
        with patch.object(sys, "argv", test_args):
            main()
            
        # Verify run_easytsf was called correctly
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        assert call_args[1]["tag"] == "test_run"
        assert call_args[0][0].dataset["unique_ids"] == ["S1"]

def test_easytsf_runner_cli_missing_config():
    """Test CLI fails without config argument."""
    with patch.object(sys, "argv", ["easytsf_runner.py"]):
        with pytest.raises(SystemExit):
            main()

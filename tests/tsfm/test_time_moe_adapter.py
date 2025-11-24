"""
Tests for Time-MoE Adapter.

Time-MoEモデルのロード、前処理、推論ロジックをテストする。
実際のモデルウェイト(HuggingFace)へのアクセスは行わず、
transformersライブラリのクラスをモックして動作確認を行う。
"""

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
import torch

# モジュールインポート前に transformers がなくてもテストが落ちないようにする
# (実際の環境では必要だが、テスト環境ではモックで済ませる場合もあるため)
sys.modules["transformers"] = MagicMock()

from nf_loto_platform.tsfm.time_moe_adapter import TimeMoEAdapter


# --- Fixtures ---

@pytest.fixture
def mock_panel_df():
    """テスト用のパネルデータを作成."""
    dates = pd.date_range(start="2023-01-01", periods=50, freq="D")
    
    # 系列1
    df1 = pd.DataFrame({
        "unique_id": "S1",
        "ds": dates,
        "y": np.random.rand(50).astype(np.float32)
    })
    
    # 系列2
    df2 = pd.DataFrame({
        "unique_id": "S2",
        "ds": dates,
        "y": np.random.rand(50).astype(np.float32) + 10
    })
    
    return pd.concat([df1, df2], ignore_index=True)


@pytest.fixture
def mock_hf_components():
    """Hugging Face関連のコンポーネントをモック化."""
    with patch("nf_loto_platform.tsfm.time_moe_adapter.AutoConfig") as MockConfig, \
         patch("nf_loto_platform.tsfm.time_moe_adapter.AutoModelForTimeSeriesForecasting") as MockModel:
        
        # Configのモック
        config_instance = MagicMock()
        config_instance.context_length = 512
        MockConfig.from_pretrained.return_value = config_instance
        
        # Modelのモック
        model_instance = MagicMock()
        # generate() メソッドの戻り値を設定 (Batch, Samples, Horizon)
        # 2系列, 1サンプル, 12ホライゾン
        mock_output = torch.rand(1, 1, 12) # バッチサイズはループ内で1つずつ処理される想定
        model_instance.generate.return_value = mock_output
        
        # forward() メソッドの戻り値 (logits)
        mock_logits = MagicMock()
        mock_logits.logits = torch.rand(1, 12, 1) # (Batch, Horizon, Dim)
        model_instance.return_value = mock_logits
        
        MockModel.from_pretrained.return_value = model_instance
        
        yield MockConfig, MockModel, model_instance


# --- Tests ---

def test_initialization():
    """初期化のパラメータ設定が正しいか."""
    adapter = TimeMoEAdapter(
        model_name="Time-MoE-50M",
        context_length=128,
        use_gpu=False
    )
    
    assert adapter.model_name == "Time-MoE-50M"
    assert adapter.context_length == 128
    assert adapter.device.type == "cpu"


def test_supports():
    """capability checkのテスト."""
    adapter = TimeMoEAdapter()
    
    # Time-MoEは基本的に単変量(Univariate)として扱うため、サポート範囲は広いはず
    assert adapter.supports(horizon=24, num_series=1, num_features=0)
    assert adapter.supports(horizon=96, num_series=100, num_features=0)


def test_predict_flow(mock_panel_df, mock_hf_components):
    """predictメソッドの正常系フローテスト."""
    _, _, mock_model = mock_hf_components
    
    adapter = TimeMoEAdapter(model_name="Time-MoE-Test", use_gpu=False)
    horizon = 12
    
    # 実行
    preds = adapter.predict(mock_panel_df, horizon=horizon)
    
    # 検証 1: 戻り値の形式
    assert isinstance(preds, pd.DataFrame)
    expected_cols = {"unique_id", "ds", "Time-MoE-Test"}
    assert expected_cols.issubset(preds.columns)
    
    # 検証 2: データ量 (2系列 * 12時点 = 24行)
    assert len(preds) == 2 * horizon
    assert preds["unique_id"].nunique() == 2
    
    # 検証 3: モデル呼び出し
    # 各系列ごとに generate が呼ばれているはず (2回)
    assert mock_model.generate.call_count == 2
    
    # 入力テンソルの形状チェック
    # 直近 context_length 分が渡されているか
    call_args = mock_model.generate.call_args_list[0]
    input_tensor = call_args.kwargs['inputs']
    # shape: (1, seq_len) -> (1, 50)
    assert input_tensor.shape == (1, 50) 


def test_predict_with_context_truncation(mock_panel_df, mock_hf_components):
    """コンテキスト長を超える入力が正しく切り詰められるか."""
    _, _, mock_model = mock_hf_components
    
    # 入力データより短い context_length を指定
    adapter = TimeMoEAdapter(context_length=10) 
    horizon = 5
    
    adapter.predict(mock_panel_df, horizon=horizon)
    
    # モデルに入力されたテンソルの長さを確認
    call_args = mock_model.generate.call_args_list[0]
    input_tensor = call_args.kwargs['inputs']
    
    # 期待: 最新の10点のみ
    assert input_tensor.shape[-1] == 10


def test_model_load_failure():
    """モデルロード失敗時のエラーハンドリング."""
    with patch("nf_loto_platform.tsfm.time_moe_adapter.AutoConfig") as MockConfig:
        MockConfig.from_pretrained.side_effect = Exception("Model not found")
        
        adapter = TimeMoEAdapter()
        
        with pytest.raises(Exception, match="Model not found"):
            adapter.predict(pd.DataFrame({"unique_id": [], "ds": [], "y": []}), horizon=1)
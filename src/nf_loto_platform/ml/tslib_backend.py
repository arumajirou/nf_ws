"""TSLib backend integration for nf_loto_platform.

現状の実装は「構造を整えた薄いラッパー」です。
将来的に TSLib を実際にインポートしてベンチマークを回すときの
足場として設計されています。

- TSLibExperimentConfig: 1 つの実験設定を表すデータクラス
- TSLibExperimentResult: 実行結果のサマリ
- run_tslib_experiment: 設定を受け取り、結果オブジェクトを返す

テスト環境や CI では、TSLib がインストールされていなくても
ImportError を投げずに安全に動くようにしてあります。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Mapping, Optional

import pandas as pd


@dataclass
class TSLibExperimentConfig:
    """TSLib 実験 1 件ぶんの設定.

    本来であれば TSLib の Dataset/Model 名や loss/metrics などを
    そのまま保持しますが、ここでは最小限のメンバーだけを定義します。
    """

    framework: str = "TSLIB"
    dataset_name: str = ""
    model_name: str = ""
    horizon: int = 1
    input_length: int = 1
    loss: str = "mse"
    metrics: Optional[list[str]] = None
    extra_params: Dict[str, Any] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # None は JSON フレンドリーな形にそろえておく
        if data["metrics"] is None:
            data["metrics"] = []
        if data["extra_params"] is None:
            data["extra_params"] = {}
        return data


@dataclass
class TSLibExperimentResult:
    """TSLib 実験の結果サマリ.

    - metrics: MAE/MAPE/RMSE 等の指標
    - history: 必要に応じて DataFrame を保持（ここではオプショナル）
    """

    config: TSLibExperimentConfig
    metrics: Mapping[str, float]
    history: Optional[pd.DataFrame] = None


def run_tslib_experiment(
    config: TSLibExperimentConfig,
    *,
    dataset: Optional[pd.DataFrame] = None,
) -> TSLibExperimentResult:
    """TSLib 実験を 1 件実行する.

    現段階では、実際の TSLib 呼び出しは行わず、
    引数検証と「ダミー結果」の生成のみを行います。

    これにより、nf_loto_platform 側では

    - 実験トラッキングのコードパス
    - ts_research スキーマへの保存ロジック
    - LLM エージェントからの呼び出し経路

    を先に実装・検証できるようにします。
    """
    if config.horizon <= 0:
        raise ValueError("horizon must be positive")
    if config.input_length <= 0:
        raise ValueError("input_length must be positive")

    # ダミーのメトリクスを返す（実装時に置き換える）
    dummy_metrics: Dict[str, float] = {
        "mae": 0.0,
        "mape": 0.0,
        "rmse": 0.0,
    }
    return TSLibExperimentResult(config=config, metrics=dummy_metrics, history=None)

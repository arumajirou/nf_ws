"""TimeSeriesScientistAgent: ts_research.* を解析する LLM エージェント.

本エージェントは、ts_research スキーマに保存された実験結果を集約し、
- モデル間比較
- エラー分布の要約
- 次の実験候補の提案

などを行う「科学者役」エージェントの骨格を提供します。

このファイルでは、DB 依存を薄く保ち、テストではモック store を利用できるように
インターフェースを意識した実装のみを行います。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Optional, Sequence


@dataclass
class TrialSuggestion:
    """次に投げるべき trial の提案情報."""

    framework: str
    model_name: str
    hyperparameters: Mapping[str, Any]


@dataclass
class ScientistReport:
    """TimeSeriesScientistAgent が生成するレポート."""

    summary_markdown: str
    recommended_trials: List[TrialSuggestion]


class TimeSeriesScientistAgent:
    """ts_research.* を読み取り分析する LLM エージェントの枠組み."""

    def __init__(self, llm_client: Any, store: Any) -> None:
        # llm_client: 既存 nf_loto_platform.agents.llm_client.LLMClient 互換を想定
        # store: nf_loto_platform.db.ts_research_store.TSResearchStore 互換を想定
        self._llm = llm_client
        self._store = store

    # ここでは store 側のインターフェースを最小限に仮定する
    def _fetch_metrics_for_experiment(self, experiment_id: int) -> Sequence[Mapping[str, Any]]:
        """実験 ID に紐づくメトリクスを取得する.

        テストではモック store がこのメソッドをオーバーライドしてもよい。
        """
        if not hasattr(self._store, "get_metrics_for_experiment"):
            return []
        return self._store.get_metrics_for_experiment(experiment_id)

    def analyze_experiment(self, experiment_id: int) -> ScientistReport:
        """単一実験の結果を深掘り分析する."""
        metrics_rows = self._fetch_metrics_for_experiment(experiment_id)

        # LLM へのプロンプト生成は、まずは単純なテキストで十分
        system_prompt = "You are an analytical scientist that summarizes time-series experiments."
        user_prompt = f"Analyze time series experiment {experiment_id} given metrics: {metrics_rows}"
        llm_response = self._llm.generate(system_prompt=system_prompt, user_prompt=user_prompt)

        # ここでは LLM 応答のうち summary だけを使い、trial 提案は空で返す
        # （将来の拡張ポイントとする）
        return ScientistReport(summary_markdown=llm_response, recommended_trials=[])

    def compare_experiments(self, experiment_ids: Iterable[int]) -> ScientistReport:
        """複数実験を比較し、傾向と推奨を出す."""
        all_metrics = {}
        for exp_id in experiment_ids:
            all_metrics[exp_id] = self._fetch_metrics_for_experiment(exp_id)
        system_prompt = "You are an analytical scientist that compares experiments."
        user_prompt = f"Compare multiple time series experiments given metrics: {all_metrics}"
        llm_response = self._llm.generate(system_prompt=system_prompt, user_prompt=user_prompt)
        return ScientistReport(summary_markdown=llm_response, recommended_trials=[])

    def suggest_next_trials(self, experiment_id: int) -> List[TrialSuggestion]:
        """次に投げるべき trial 候補を返す.

        現段階では、LLM 応答を解析せず、空リストを返す。
        実運用フェーズで JSON 形式の応答をパースするように拡張する。
        """
        _ = experiment_id
        return []

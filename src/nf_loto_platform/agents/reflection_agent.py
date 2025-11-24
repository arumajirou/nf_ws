"""
Reflection Agent implementation.

実行された実験の結果（メトリクス、予測区間、学習曲線）を評価し、
「品質保証」の観点から批評（Critique）を行うエージェント。

主な役割:
1. 実験結果が成功か失敗かを判定する
2. 過学習や未学習、予測区間の広すぎ/狭すぎなどの問題を診断する
3. 次回のイテレーションに向けた具体的な改善案（パラメータ変更など）を提示する
4. ビジネス指標（方向正解率、最大ドローダウン等）を含めた多面的な評価を行う
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from nf_loto_platform.agents.llm_client import LLMClient
from nf_loto_platform.ml.model_runner import ExperimentResult

logger = logging.getLogger(__name__)


class ReflectionAgent:
    """
    Result Reflector & QA Specialist Agent.
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: agent_config.yaml の内容
        """
        self.config = config
        self.agent_config = config.get("agents", {}).get("reflection", {})
        self.llm = LLMClient(config)
        
        self.name = self.agent_config.get("name", "ResultReflector")
        self.system_prompt = self.agent_config.get(
            "system_prompt", 
            "You are a QA specialist. Evaluate the forecasting model performance from both technical and business perspectives."
        )

    def evaluate(
        self,
        result: ExperimentResult,
        goal_metric: str,
        history: List[Dict[str, Any]]
    ) -> Tuple[str, bool]:
        """
        実験結果を評価し、批評と満足度（終了判定）を返す。

        Args:
            result: 今回の実験結果
            goal_metric: 最適化対象のメトリクス名 (例: 'mae')
            history: 過去のイテレーション履歴

        Returns:
            critique (str): 評価コメントと改善案
            is_satisfied (bool): これで十分（終了）と判断したかどうか
        """
        logger.info(f"[{self.name}] Starting evaluation...")

        # 1. 定量データの準備
        metrics = result.meta.get("metrics", {})
        model_name = result.meta.get("model_name", "Unknown")
        best_params = result.meta.get("best_params", {})
        uncertainty = result.meta.get("uncertainty_metrics", {})
        
        # エラーチェック
        if not metrics and "error" in result.meta:
            return f"Experiment failed with error: {result.meta['error']}", False

        # 主要メトリクスの取得
        current_score = metrics.get(goal_metric)
        if current_score is None:
            # goal_metric が見つからない場合は mae や loss で代用
            current_score = metrics.get("mae", metrics.get("loss", float("inf")))

        # 2. ルールベース診断 (LLMの判断材料を作成)
        diagnosis = self._diagnose_issues(metrics, uncertainty)
        
        # 3. 履歴比較
        comparison_text = "This is the first iteration."
        if history:
            prev_best = min([h["score"] for h in history])
            if current_score < prev_best:
                comparison_text = f"IMPROVEMENT: Score improved from {prev_best:.4f} to {current_score:.4f}."
            else:
                comparison_text = f"REGRESSION: Score worsened (best was {prev_best:.4f}, current is {current_score:.4f})."

        # 4. LLMプロンプト構築
        user_prompt = self._build_reflection_prompt(
            model_name=model_name,
            params=best_params,
            metrics=metrics,
            uncertainty=uncertainty,
            diagnosis=diagnosis,
            comparison=comparison_text,
            goal_metric=goal_metric
        )

        # 5. LLM実行
        response_text = self.llm.chat_completion(
            system_prompt=self.system_prompt,
            user_prompt=user_prompt
        )

        # 6. 終了判定の解析
        is_satisfied = False
        
        # ルールベースの強制終了条件
        # 方向正解率が極めて高い、または誤差が極小
        da = metrics.get("directional_accuracy", 0)
        if current_score < 1e-5 or da > 0.95: 
            is_satisfied = True
        
        # LLMの判定を優先
        if "SATISFIED" in response_text.upper() or "NO FURTHER IMPROVEMENT" in response_text.upper():
            is_satisfied = True
            
        # 履歴からの判断: 連続して改善が見られない場合
        if len(history) >= 2:
            last_scores = [h["score"] for h in history[-2:]]
            if all(s <= current_score for s in last_scores):
                 logger.info(f"[{self.name}] Detection stagnation. Suggesting stop.")

        logger.info(f"[{self.name}] Evaluation done. Satisfied={is_satisfied}")
        return response_text, is_satisfied

    def _diagnose_issues(self, metrics: Dict[str, float], uncertainty: Dict[str, Any]) -> List[str]:
        """
        メトリクスから典型的な問題を診断する。
        MAEだけでなく、Directional AccuracyやCoverageも考慮する。
        """
        issues = []
        
        # 1. 過学習チェック (Train vs Val)
        train_loss = metrics.get("train_loss_step") or metrics.get("train_loss")
        val_loss = metrics.get("val_loss")
        
        if train_loss is not None and val_loss is not None:
            if val_loss > train_loss * 2.0:
                issues.append(f"Potential Overfitting: Validation loss ({val_loss:.4f}) is much higher than training loss ({train_loss:.4f}). Suggest increasing regularization (dropout, weight_decay).")
        
        # 2. 方向正解率 (Directional Accuracy) の評価
        da = metrics.get("directional_accuracy")
        if da is not None:
            if da < 0.5:
                issues.append(f"Critical: Directional Accuracy ({da:.2f}) is worse than random guess. Model captures trend incorrectly.")
            elif da < 0.6:
                issues.append(f"Weak Trend Capture: Directional Accuracy ({da:.2f}) is low. Consider using trend-aware models like DLinear.")

        # 3. 最大ドローダウン (Max Drawdown) - リスク評価
        mdd = metrics.get("max_drawdown")
        if mdd is not None and mdd > 0.5: # 50%以上の下落予測
             issues.append(f"High Risk Forecast: Predicted Max Drawdown is {mdd:.2f}. Verify if this matches historical volatility.")

        # 4. 不確実性チェック (Conformal Prediction Coverage)
        coverage = uncertainty.get("coverage_rate")
        if coverage is not None:
            target_coverage = 0.9 # 仮定
            if coverage < target_coverage - 0.15:
                issues.append(f"Under-confident: Actual coverage ({coverage:.2f}) is significantly lower than target ({target_coverage}). Prediction intervals are too narrow (Risk of unexpected outliers).")
            elif coverage > target_coverage + 0.09:
                issues.append(f"Over-confident: Actual coverage ({coverage:.2f}) is too high. Prediction intervals are uselessly wide.")

        # 5. 異常値チェック (極端なLoss)
        if metrics.get("mae", 0) > 10000: 
            issues.append("Suspiciously high error. Check if data normalization is working correctly.")

        return issues

    def _build_reflection_prompt(
        self,
        model_name: str,
        params: Dict[str, Any],
        metrics: Dict[str, float],
        uncertainty: Dict[str, Any],
        diagnosis: List[str],
        comparison: str,
        goal_metric: str
    ) -> str:
        """LLM向けプロンプトを作成. ビジネス指標を強調して提示する."""
        
        diagnosis_str = "\n".join([f"- {d}" for d in diagnosis]) if diagnosis else "- No obvious technical issues detected."
        
        # ビジネス関連メトリクスの抽出
        biz_metrics = {
            "Directional Accuracy": metrics.get("directional_accuracy", "N/A"),
            "Max Drawdown": metrics.get("max_drawdown", "N/A"),
            "Sharpe Ratio": metrics.get("sharpe_ratio", "N/A")
        }

        prompt = f"""
## Experiment Result
- **Model**: {model_name}
- **Goal Metric ({goal_metric})**: {metrics.get(goal_metric, 'N/A')}
- **Standard Metrics**: {json.dumps({k:v for k,v in metrics.items() if k not in ['directional_accuracy', 'max_drawdown', 'sharpe_ratio'] and k != goal_metric}, indent=2)}

## Business & Risk Metrics
{json.dumps(biz_metrics, indent=2)}

## Uncertainty & Coverage
{json.dumps(uncertainty, indent=2)}

## Hyperparameters Used
{json.dumps(params, indent=2)}

## Comparison with History
{comparison}

## Automated Diagnosis
{diagnosis_str}

## Your Task (Critique)
You are a Senior Data Scientist evaluating this time series model.
1. **Performance Assessment**:
   - Is the error ({goal_metric}) acceptable?
   - **Crucial**: Does the model predict the direction correctly? (See Directional Accuracy)
   - Is the predicted risk (Drawdown) reasonable?
2. **Strategy Validation**: Did the chosen model/parameters work?
3. **Actionable Improvements**: What specific parameters or model types should be tried next?
   - If Directional Accuracy is low, suggest trend-focused models or features.
   - If Overfitting, suggest regularization.
4. **Go/No-Go Decision**: 
   - If metrics are satisfactory for deployment, conclude with "SATISFIED".
   - Otherwise, propose the next experiment.

Please ensure your feedback addresses both accuracy (MAE/RMSE) and business utility (Direction, Risk).
"""
        return prompt
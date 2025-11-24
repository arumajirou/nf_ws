"""
Reflection Agent implementation.

実行された実験の結果（メトリクス、予測区間、学習曲線）を評価し、
「品質保証」の観点から批評（Critique）を行うエージェント。

主な役割:
1. 実験結果が成功か失敗かを判定する
2. 過学習や未学習、予測区間の広すぎ/狭すぎなどの問題を診断する
3. 次回のイテレーションに向けた具体的な改善案（パラメータ変更など）を提示する
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
            "You are a QA specialist. Evaluate the forecasting model performance."
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
        # LLMの出力に "SATISFIED" または "STOP LOOP" が含まれていれば終了とするルール、
        # あるいは JSON形式で出力させるのが確実だが、ここでは簡易的にテキスト解析する。
        is_satisfied = False
        
        # ルールベースの強制終了条件 (例: score が非常に良い、あるいはNaN)
        if current_score < 1e-5: # 完全一致に近い
            is_satisfied = True
        
        # LLMの判定を優先
        if "SATISFIED" in response_text.upper() or "NO FURTHER IMPROVEMENT" in response_text.upper():
            is_satisfied = True
            
        # 履歴からの判断: 連続して改善が見られない場合
        if len(history) >= 2:
            # 直近2回が改善していないなら諦める
            last_scores = [h["score"] for h in history[-2:]]
            if all(s <= current_score for s in last_scores):
                 logger.info(f"[{self.name}] Detection stagnation. Suggesting stop.")
                 # ここでは自動的にTrueにはせず、LLMに委ねるが、Critiqueに含めるべき

        logger.info(f"[{self.name}] Evaluation done. Satisfied={is_satisfied}")
        return response_text, is_satisfied

    def _diagnose_issues(self, metrics: Dict[str, float], uncertainty: Dict[str, Any]) -> List[str]:
        """
        メトリクスから典型的な問題を診断する。
        """
        issues = []
        
        # 1. 過学習チェック (Train vs Val)
        train_loss = metrics.get("train_loss_step") or metrics.get("train_loss")
        val_loss = metrics.get("val_loss")
        
        if train_loss is not None and val_loss is not None:
            if val_loss > train_loss * 2.0:
                issues.append(f"Potential Overfitting: Validation loss ({val_loss:.4f}) is much higher than training loss ({train_loss:.4f}). Suggest increasing regularization (dropout, weight_decay).")
        
        # 2. 不確実性チェック (Conformal Prediction)
        # uncertainty_metrics: {'score': float, 'coverage_rate': float, ...}
        coverage = uncertainty.get("coverage_rate")
        if coverage is not None:
            target_coverage = 0.9 # 仮定
            if coverage < target_coverage - 0.15:
                issues.append(f"Under-confident: Actual coverage ({coverage:.2f}) is significantly lower than target ({target_coverage}). Prediction intervals are too narrow.")
            elif coverage > target_coverage + 0.09:
                issues.append(f"Over-confident: Actual coverage ({coverage:.2f}) is too high. Prediction intervals are uselessly wide.")

        # 3. 異常値チェック (極端なLoss)
        if metrics.get("mae", 0) > 10000: # データのスケールによるが...
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
        """LLM向けプロンプトを作成."""
        
        diagnosis_str = "\n".join([f"- {d}" for d in diagnosis]) if diagnosis else "- No obvious technical issues detected."
        
        prompt = f"""
## Experiment Result
- **Model**: {model_name}
- **Goal Metric ({goal_metric})**: {metrics.get(goal_metric, 'N/A')}
- **Other Metrics**: {json.dumps({k:v for k,v in metrics.items() if k != goal_metric}, indent=2)}
- **Uncertainty/Coverage**: {json.dumps(uncertainty, indent=2)}

## Hyperparameters Used
{json.dumps(params, indent=2)}

## Comparison with History
{comparison}

## Technical Diagnosis (Rule-based)
{diagnosis_str}

## Your Task (Critique)
1. **Assess Performance**: Is this model performing well? Is the error acceptable?
2. **Validate Strategy**: Did the chosen model/parameters work as expected?
3. **Suggest Improvements**: What should be changed in the next iteration? 
   - Examples: "Increase input_size", "Switch to TSFM (Zero-shot) due to overfitting", "Add exogenous variables".
4. **Decision**: Should we continue optimizing or stop? 
   - If the result is good enough or improvements are unlikely, say "SATISFIED".
   - Otherwise, provide a specific plan for the next step.

Please keep your response concise and actionable.
"""
        return prompt
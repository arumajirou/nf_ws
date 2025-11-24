from __future__ import annotations

from typing import Dict, List, Mapping, Any

from nf_loto_platform.ml.model_registry import AUTO_MODEL_REGISTRY

from .domain import CuratorOutput, ExperimentRecipe, TimeSeriesTaskSpec


class PlannerAgent:
    """
    モデル/特徴量の組合せから実験レシピを構築するエージェント.
    データ特性(データ量、季節性、トレンド等)に基づき動的にモデルを選択・優先順位付けします。
    """

    def __init__(self, registry: Mapping[str, object] | None = None) -> None:
        # AUTO_MODEL_REGISTRY は {name: AutoModelSpec}
        self._registry = registry or AUTO_MODEL_REGISTRY

    def _get_allowed_candidates(self, task: TimeSeriesTaskSpec) -> List[str]:
        """ユーザー設定(allowフラグ)に基づいて候補モデルをフィルタリングする."""
        names: List[str] = []
        for name, spec in self._registry.items():  # type: ignore[assignment]
            engine_kind = getattr(spec, "engine_kind", "")  # tsfm / neuralforecast / classical
            
            # ライブラリ依存の強いモデルはここでは見ない (registry 側で制御)
            if not getattr(spec, "enabled", True):
                continue

            if engine_kind == "tsfm" and task.allow_tsfm:
                names.append(name)
            elif engine_kind == "neuralforecast" and task.allow_neuralforecast:
                names.append(name)
            elif engine_kind == "classical" and task.allow_classical:
                names.append(name)
        
        return names

    def _score_models(self, candidates: List[str], curator: CuratorOutput) -> List[str]:
        """
        データ特性に基づいてモデルにスコアを付け、推奨順にソートする。
        
        Heuristics:
        1. データ量が少ない (n_obs < 1000) -> Foundation Models (TSFM) を優先 (Zero-shot/Few-shot能力)
        2. 季節性が強い -> NHITS, TFT, SeasonalNaive を優先
        3. トレンドが強い -> DLinear, Trend-aware models を優先
        """
        # 初期スコアはすべて1.0
        scores: Dict[str, float] = {name: 1.0 for name in candidates}
        
        # Curatorの分析結果を取得
        # CuratorOutputには dataset_properties (Dict) が含まれていると想定
        # 含まれていない場合は安全にデフォルト値を使用
        props: Dict[str, Any] = getattr(curator, "dataset_properties", {})
        
        # データ特性の抽出 (キーが存在しない場合は安全なデフォルト値)
        n_obs = props.get("n_obs", 2000)  # デフォルトは十分なデータ量と仮定
        seasonality_strength = props.get("seasonality_strength", 0.0) # 0.0 ~ 1.0
        trend_strength = props.get("trend_strength", 0.0) # 0.0 ~ 1.0
        
        for name in candidates:
            spec = self._registry.get(name)
            engine_kind = getattr(spec, "engine_kind", "")
            # モデル名やfamily属性から特性を推測 (小文字比較)
            name_lower = name.lower()
            
            # --- Rule 1: データ量に基づく判定 ---
            if n_obs < 1000:
                # データが少ない場合、事前学習済みモデル(TSFM)やシンプルなモデルを優先
                if engine_kind == "tsfm":
                    scores[name] += 2.0
                elif engine_kind == "classical":
                    scores[name] += 1.0
                elif engine_kind == "neuralforecast":
                    # 深層学習モデル(スクラッチ学習)はデータが必要なため優先度を下げる
                    scores[name] -= 0.5
            
            # --- Rule 2: 季節性に基づく判定 ---
            if seasonality_strength > 0.6:
                # 季節性が強いデータセットの場合
                if "nhits" in name_lower or "tft" in name_lower or "seasonal" in name_lower:
                    scores[name] += 1.5
            
            # --- Rule 3: トレンドに基づく判定 ---
            if trend_strength > 0.6:
                # トレンドが強い場合
                if "dlinear" in name_lower or "nlinear" in name_lower or "trend" in name_lower:
                    scores[name] += 1.5

        # スコアの高い順にソート (スコアが同じなら名前順)
        # reverse=Trueなのでスコアが大きいものが先頭に来る
        sorted_candidates = sorted(candidates, key=lambda x: (scores[x], x), reverse=True)
        return sorted_candidates

    def plan(self, task: TimeSeriesTaskSpec, curator: CuratorOutput) -> ExperimentRecipe:
        """CuratorOutput を踏まえて実験レシピを組み立てる."""
        
        # 1. ユーザー許可設定によるフィルタリング
        candidates = self._get_allowed_candidates(task)
        
        # 2. データ特性による動的な優先順位付け (Dynamic Scoring)
        models = self._score_models(candidates, curator)

        # 3. バックエンドの決定
        backend = "local"
        if task.max_training_time_minutes and task.max_training_time_minutes > 60:
            # 時間が潤沢(60分以上)なら Optuna / Ray などの分散バックエンドを許可
            backend = "optuna"

        extra: Dict[str, object] = {
            "target_horizon": task.target_horizon,
            "validation_scheme": curator.recommended_validation_scheme,
            "planning_strategy": "dynamic_heuristics", # ログ用に戦略を記録
        }

        return ExperimentRecipe(
            models=models,
            feature_sets=curator.candidate_feature_sets,
            search_backend=backend,
            num_samples=1,
            time_budget_hours=(task.max_training_time_minutes / 60.0 if task.max_training_time_minutes else None),
            use_tsfm=task.allow_tsfm,
            use_neuralforecast=task.allow_neuralforecast,
            use_classical=task.allow_classical,
            extra_params=extra,
        )
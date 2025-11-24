from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class TimeSeriesTaskSpec:
    """時系列予測タスクの要件定義."""
    target_horizon: int
    max_training_time_minutes: Optional[int] = None
    allow_tsfm: bool = True
    allow_neuralforecast: bool = True
    allow_classical: bool = True

@dataclass
class CuratorOutput:
    """データ分析・キュレーション結果."""
    dataset_properties: Dict[str, Any] = field(default_factory=dict)
    recommended_validation_scheme: str = "holdout"
    candidate_feature_sets: List[str] = field(default_factory=list)

@dataclass
class ExperimentRecipe:
    """実験実行レシピ (Plannerの出力)."""
    models: List[str]
    feature_sets: List[str]
    search_backend: str
    num_samples: int
    time_budget_hours: Optional[float]
    use_tsfm: bool
    use_neuralforecast: bool
    use_classical: bool
    extra_params: Dict[str, Any] = field(default_factory=dict)
    
    # 互換性のため辞書化メソッドを提供
    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.models[0] if self.models else "AutoNHITS",
            "backend": self.search_backend,
            "num_samples": self.num_samples,
            "model_params": self.extra_params
        }
    
    # 辞書のようにアクセスできるようにする (Orchestrator互換)
    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)
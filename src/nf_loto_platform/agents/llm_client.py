from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

class BaseLLMClient(ABC):
    """LLM 呼び出しを抽象化する最小限のインターフェイス."""

    @abstractmethod
    def chat_completion(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        """LLM から 1 つのテキスト応答を生成する (Chat API)."""
        raise NotImplementedError

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """メッセージ履歴全体を受け取って応答を生成する."""
        raise NotImplementedError


class EchoLLMClient(BaseLLMClient):
    """テスト用の簡易 LLM クライアント (モック)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.max_chars = self.config.get("max_chars", 1024)

    def chat_completion(self, system_prompt: str, user_prompt: str, **kwargs: Any) -> str:
        logger.info(f"[EchoLLM] System: {system_prompt[:50]}... / User: {user_prompt[:50]}...")
        # テスト用に「満足した」旨のレスポンスを返す場合がある
        if "evaluate" in system_prompt.lower() or "critique" in user_prompt.lower():
             return "The model performance is acceptable based on the metrics provided. SATISFIED."
        
        combined = f"[MOCK RESPONSE]\nBased on context:\n{system_prompt}\n---\n{user_prompt}"
        return combined[:self.max_chars]

    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        logger.info(f"[EchoLLM] Chat with {len(messages)} messages")
        return f"[MOCK CHAT RESPONSE] Received {len(messages)} messages."


# 実装クラスとして公開 (本番では OpenAIClient などに差し替える)
LLMClient = EchoLLMClient
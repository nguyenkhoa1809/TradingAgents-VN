"""LLM provider abstraction — swap Claude / DeepSeek qua config.

Cả hai đều chỉ cần API key + model name. DeepSeek dùng OpenAI-compatible SDK.
"""
import os
import json
from abc import ABC, abstractmethod
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, prompt: str, max_tokens: int = 800) -> dict:
        """Gọi LLM, kỳ vọng JSON output, parse và return dict."""
        ...


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


class ClaudeProvider(LLMProvider):
    def __init__(self, model: str = "claude-haiku-4-5"):
        import anthropic
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        self.model = model

    def complete_json(self, prompt: str, max_tokens: int = 800) -> dict:
        import time
        last_err = None
        for attempt in range(3):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            if resp.stop_reason == "max_tokens":
                max_tokens = int(max_tokens * 1.5)
                time.sleep(0.5)
                continue
            try:
                return json.loads(_strip_json_fences(resp.content[0].text))
            except json.JSONDecodeError as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise ValueError(f"Failed after 3 attempts: {last_err}")


# Pro/reasoner không hỗ trợ json_object mode; flash hỗ trợ (verified).
_NO_JSON_MODE = {"deepseek-v4-pro", "deepseek-reasoner"}


class DeepSeekProvider(LLMProvider):
    """DeepSeek API — OpenAI-compatible. base_url = https://api.deepseek.com"""

    def __init__(self, model: str = "deepseek-v4-flash"):
        from openai import OpenAI
        self.client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )
        self.model = model
        self._use_json_mode = model not in _NO_JSON_MODE

    def complete_json(self, prompt: str, max_tokens: int = 800) -> dict:
        import time
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if self._use_json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        last_err = None
        for attempt in range(3):
            resp = self.client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            finish_reason = resp.choices[0].finish_reason
            if not content.strip():
                time.sleep(1.5 * (attempt + 1))
                continue
            # max_tokens bị hit → JSON bị cắt → tăng budget và retry
            if finish_reason == "length":
                kwargs["max_tokens"] = int(kwargs["max_tokens"] * 1.5)
                time.sleep(0.5)
                continue
            try:
                return json.loads(_strip_json_fences(content))
            except json.JSONDecodeError as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        raise ValueError(f"Failed after 3 attempts: {last_err}")


def get_provider(name: str, model: str = None) -> LLMProvider:
    """Factory. name in {'claude', 'deepseek'}."""
    if name == "claude":
        return ClaudeProvider(model or "claude-haiku-4-5")
    elif name == "deepseek":
        return DeepSeekProvider(model or "deepseek-v4-flash")
    raise ValueError(f"Unknown provider: {name}")

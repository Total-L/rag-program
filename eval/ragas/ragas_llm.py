"""RAGAS-协议兼容的 mmx LLM 包装（ENTERPRISE_AUDIT C1）。

设计目标：
- 兼容 RAGAS 0.2.x `BaseRagasLLM` 接口（`generate_text` / `agenerate_text`）
- 默认走 `mmx` CLI（与 P1 `llm_judge.py` 一致）；可通过 env 切到 ollama
- 当真实 `ragas>=0.2,<0.3` 安装就位后,可通过 `RAGAS_USE_REAL=1` 切回真库
  路径,eval/runner.py 检测到后自动 shim。

为什么不在 requirements 里只装 ragas:
- 当前开发机 pypi 网络不稳(langsmith/pyarrow 经常 timeout),C1 不要等真库
- 实现遵循 RAGAS 协议,后续替换一行 import 即可

用法(与真 ragas 一致):
    from eval.ragas.ragas_llm import MmxRagasLLM
    llm = MmxRagasLLM(model="MiniMax-M3")
    out = llm.generate_text("good or poor?")
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from typing import Any


class MmxRagasLLM:
    """mmx-backed RAGAS LLM。

    公开两个方法（与 `ragas.llms.BaseRagasLLM` 0.2.x 一致）:
    - `generate_text(prompt, **kwargs) -> str`
    - `agenerate_text(prompt, **kwargs) -> str`

    注：RAGAS 0.2 期望返回 `str`。0.3+ 改成返回 `LangchainLLMResult-like` 对象。
    我们对齐 0.2 接口,这样 pyproject 的 `ragas>=0.2,<0.3` 真装上后可直接继承。
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        mmx_bin: str | None = None,
        timeout: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self.model = model or os.environ.get("MMX_MODEL", "MiniMax-M3")
        self.mmx_bin = (
            mmx_bin
            or os.environ.get("MMX_BIN")
            or "/opt/homebrew/bin/mmx"
        )
        self.timeout = timeout or int(os.environ.get("MMX_TIMEOUT", "30"))
        self.temperature = (
            temperature if temperature is not None
            else float(os.environ.get("MMX_TEMPERATURE", "0.0"))
        )
        self.max_tokens = (
            max_tokens if max_tokens is not None
            else int(os.environ.get("MMX_MAX_TOKENS", "256"))
        )

    # ── RAGAS API surface ──

    def generate_text(self, prompt: str, **kwargs: Any) -> str:
        return self._mmx_chat(prompt, **kwargs)

    async def agenerate_text(self, prompt: str, **kwargs: Any) -> str:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: self._mmx_chat(prompt, **kwargs)
        )

    # ── mmx CLI 调用 ──

    def _mmx_chat(self, prompt: str, **overrides: Any) -> str:
        cmd = [
            self.mmx_bin,
            "text",
            "chat",
            "--model",
            overrides.get("model", self.model),
            "--max-tokens",
            str(overrides.get("max_tokens", self.max_tokens)),
            "--temperature",
            str(overrides.get("temperature", self.temperature)),
            "--message",
            prompt,
            "--output",
            "json",
            "--quiet",
        ]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except FileNotFoundError as e:
            raise RuntimeError(
                f"mmx binary not found at {self.mmx_bin!r}; "
                "set MMX_BIN env or install mmx CLI"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"mmx timeout after {self.timeout}s") from e

        if r.returncode != 0:
            # 失败:返回空字符串(由 metric 层兜底成 0 分)
            return ""

        raw = r.stdout.strip()
        if not raw:
            return ""
        # mmx JSON 输出格式因 version 而异(text 字段 / Anthropic content[0].text)
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            return raw  # 非 JSON 时直接返回原文(metric 层兜底)

        if isinstance(d, dict):
            # Anthropic-ish
            content = d.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict):
                return content[0].get("text", "")
            # openai-ish
            choices = d.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message")
                if isinstance(msg, dict):
                    return msg.get("content", "")
            # mmx-native
            return d.get("text") or d.get("answer") or ""
        return str(d)


__all__ = ["MmxRagasLLM"]

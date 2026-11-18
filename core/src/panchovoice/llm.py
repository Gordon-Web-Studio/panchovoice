"""LLM providers for the AI caller and the judge (and later Panchito).

Interface: `complete(system, messages, json_schema_hint=None) -> str`.
Every provider counts usage and stops at `max_calls_per_run` (LLMBudgetError).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    pass


class LLMBudgetError(LLMError):
    pass


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd_estimate: float = 0.0
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


Message = dict[str, str]  # {"role": "user"|"assistant", "content": "..."}


def extract_json(text: str) -> Any:
    """Return the first valid JSON object (or array) inside `text`. Raises ValueError if none."""
    if text is None:
        raise ValueError("no text")
    s = text.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(s, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON object in reply: {s[:200]!r}")


class LLMProvider:
    name = "base"

    def __init__(self, model: str, max_calls: int = 60):
        self.model = model
        self.max_calls = max_calls
        self.usage = Usage()
        self._lock = threading.Lock()

    def _reserve_call(self) -> None:
        with self._lock:
            if self.usage.calls >= self.max_calls:
                raise LLMBudgetError(f"max_calls_per_run reached ({self.max_calls})")
            self.usage.calls += 1

    def complete(self, system: str, messages: list[Message], json_schema_hint: str | None = None) -> str:
        self._reserve_call()
        try:
            return self._complete(system, messages, json_schema_hint)
        except Exception:
            self.usage.errors += 1
            raise

    def complete_json(self, system: str, messages: list[Message], json_schema_hint: str | None = None) -> Any:
        return extract_json(self.complete(system, messages, json_schema_hint))

    def _complete(self, system: str, messages: list[Message], json_schema_hint: str | None) -> str:
        raise NotImplementedError


def _json_rule(hint: str | None) -> str:
    if not hint:
        return ""
    return ("\n\nAnswer with ONE strict JSON object only, no prose, no code fences. "
            f"Shape: {hint}")


def _render_conversation(messages: list[Message]) -> str:
    """For single prompt providers: the conversation as plain text."""
    if len(messages) == 1 and messages[0].get("role") == "user":
        return messages[0]["content"]
    lines = []
    for m in messages:
        role = "USER" if m.get("role") == "user" else "ASSISTANT"
        lines.append(f"[{role}]\n{m.get('content', '')}")
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- claude-cli


class ClaudeCLIProvider(LLMProvider):
    """Runs `claude -p` on the user's subscription. Prompt on stdin, system prompt from a file."""

    name = "claude-cli"

    def __init__(self, model: str = "haiku", max_calls: int = 60, timeout: float = 120.0, exe: str | None = None):
        super().__init__(model, max_calls)
        self.timeout = timeout
        self.exe = exe or shutil.which("claude")
        self._cwd = Path(tempfile.gettempdir()) / "panchovoice-llm"
        self._cwd.mkdir(parents=True, exist_ok=True)

    def _complete(self, system: str, messages: list[Message], json_schema_hint: str | None) -> str:
        if not self.exe:
            raise LLMError("the `claude` command is not on PATH (install Claude Code or choose another llm.provider)")
        prompt = _render_conversation(messages) + _json_rule(json_schema_hint)
        sys_file = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8", dir=self._cwd)
        try:
            sys_file.write(system)
            sys_file.close()
            cmd = [
                self.exe, "-p",
                "--model", self.model,
                "--output-format", "json",
                "--system-prompt-file", sys_file.name,
                "--tools", "",
                "--setting-sources", "",
                "--strict-mcp-config",
                "--safe-mode",
                "--no-session-persistence",
            ]
            env = dict(os.environ)
            env.pop("CLAUDECODE", None)  # allow running from inside a Claude Code session
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", cwd=str(self._cwd), timeout=self.timeout, env=env)
            except subprocess.TimeoutExpired as exc:
                raise LLMError(f"claude -p timed out after {self.timeout:.0f} s") from exc
        finally:
            try:
                os.unlink(sys_file.name)
            except OSError:
                pass
        if proc.returncode != 0 and not proc.stdout.strip():
            raise LLMError(f"claude -p failed (exit {proc.returncode}): {(proc.stderr or '')[:400]}")
        return self._parse_envelope(proc.stdout)

    def _parse_envelope(self, out: str) -> str:
        try:
            env = extract_json(out)
        except ValueError as exc:
            raise LLMError(f"claude -p gave no JSON envelope: {out[:300]!r}") from exc
        events = env if isinstance(env, list) else [env]
        result = None
        for ev in events:
            if isinstance(ev, dict) and ev.get("type") == "result":
                result = ev
        if result is None:
            raise LLMError("claude -p envelope has no result event")
        if result.get("is_error"):
            raise LLMError(f"claude -p error: {str(result.get('result'))[:300]}")
        usage = result.get("usage") or {}
        self.usage.input_tokens += int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0)
        self.usage.output_tokens += int(usage.get("output_tokens") or 0)
        # subscription: no extra spend, so the estimate stays 0
        return str(result.get("result") or "")


# --------------------------------------------------------------------------- anthropic

MODEL_ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
# USD per million tokens (input, output). Estimates; check current pricing.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}


class AnthropicProvider(LLMProvider):
    """Messages API over raw HTTP (httpx) with ANTHROPIC_API_KEY. Hard stop at budget_usd."""

    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    # spend is shared by every provider instance in this process (one budget for the whole tool run)
    _spent_usd = 0.0
    _spent_lock = threading.Lock()

    def __init__(self, model: str = "haiku", max_calls: int = 60, budget_usd: float = 10.0,
                 api_key: str | None = None, timeout: float = 120.0):
        super().__init__(MODEL_ALIASES.get(model, model), max_calls)
        self.budget_usd = budget_usd
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.timeout = timeout

    @classmethod
    def spent(cls) -> float:
        return cls._spent_usd

    def _complete(self, system: str, messages: list[Message], json_schema_hint: str | None) -> str:
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        with self._spent_lock:
            if AnthropicProvider._spent_usd >= self.budget_usd:
                raise LLMBudgetError(f"budget_usd reached (estimated ${AnthropicProvider._spent_usd:.4f})")
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "system": system + _json_rule(json_schema_hint),
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            r = httpx.post(self.URL, json=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"Anthropic API not reachable: {exc}") from exc
        if r.status_code != 200:
            raise LLMError(f"Anthropic API error {r.status_code}: {r.text[:300]}")
        data = r.json()
        if data.get("stop_reason") == "refusal":
            raise LLMError("the model declined the request (refusal)")
        u = data.get("usage") or {}
        tin = int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
        tout = int(u.get("output_tokens") or 0)
        pin, pout = PRICES.get(self.model, (4.00, 20.00))
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        self.usage.input_tokens += tin
        self.usage.output_tokens += tout
        self.usage.usd_estimate += cost
        with self._spent_lock:
            AnthropicProvider._spent_usd += cost
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


# --------------------------------------------------------------------------- ollama


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str = "qwen2.5:7b", max_calls: int = 60, url: str = "http://localhost:11434",
                 timeout: float = 180.0):
        super().__init__(model, max_calls)
        self.url = url.rstrip("/")
        self.timeout = timeout

    def _complete(self, system: str, messages: list[Message], json_schema_hint: str | None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system + _json_rule(json_schema_hint)}, *messages],
            "stream": False,
            "options": {"temperature": 0.4},
        }
        if json_schema_hint:
            body["format"] = "json"
        try:
            r = httpx.post(f"{self.url}/api/chat", json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama not reachable at {self.url}: {exc}") from exc
        if r.status_code != 200:
            raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
        data = r.json()
        self.usage.input_tokens += int(data.get("prompt_eval_count") or 0)
        self.usage.output_tokens += int(data.get("eval_count") or 0)
        return (data.get("message") or {}).get("content", "")


# --------------------------------------------------------------------------- fake (tests)


class FakeProvider(LLMProvider):
    """Test provider: `responder(system, messages)` returns the reply text."""

    name = "fake"

    def __init__(self, responder: Callable[[str, list[Message]], str] | None = None, fail: bool = False,
                 max_calls: int = 1000):
        super().__init__("fake", max_calls)
        self.responder = responder or (lambda s, m: "{}")
        self.fail = fail
        self.prompts: list[tuple[str, list[Message]]] = []

    def _complete(self, system: str, messages: list[Message], json_schema_hint: str | None) -> str:
        self.prompts.append((system, messages))
        if self.fail:
            raise LLMError("fake provider failure")
        return self.responder(system, messages)


def make_provider(cfg: LLMConfig) -> LLMProvider:
    if cfg.provider == "claude-cli":
        return ClaudeCLIProvider(cfg.model, cfg.max_calls_per_run)
    if cfg.provider == "anthropic":
        return AnthropicProvider(cfg.model, cfg.max_calls_per_run, cfg.budget_usd)
    if cfg.provider == "ollama":
        return OllamaProvider(cfg.model, cfg.max_calls_per_run, cfg.url or "http://localhost:11434")
    raise LLMError(f"unknown llm provider {cfg.provider!r}")

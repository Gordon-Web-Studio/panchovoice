"""Claude brain: the same Brain (claim guard, confirmation gate, tool rounds), with the model call
going to the Claude API instead of Ollama.

Brain keeps its history in Ollama's shape (system / user / assistant with tool_calls / tool).
This module converts that history to Messages API format on every round and turns Claude's
stream back into Ollama-shaped chunks, so Brain.respond() runs unchanged.

The key comes from the environment (ANTHROPIC_API_KEY, or an `ant auth login` profile), never a file.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncIterator

import anthropic
import httpx

from .brain import Brain, BrainError

DEFAULT_CLAUDE_MODEL = "claude-haiku-5-5"
MAX_TOKENS = 1024  # replies are one or two spoken sentences; tool arguments are small

# Added to the system prompt for Claude only. Thinking is off for speed (a voice turn), and this
# keeps tool calls as real tool_use blocks instead of text.
CLAUDE_NOTE = (
    "\n\nWhen you use a tool, you may say a brief sentence first. If no tool can do what the caller "
    "asked for, say so instead of guessing. Do not include internal or system XML tags in your reply."
)
CALL_CONNECTED = "(The call has connected.)"


def to_claude_tools(ollama_tools: list[dict]) -> list[dict]:
    out = []
    for t in ollama_tools:
        fn = t.get("function") or {}
        out.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def to_claude_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Ollama-shaped history -> (system prompt, Messages API messages).

    - The first system message is the system prompt. Later system notes (guard, nudge) go into the
      next user turn as a marked note, so the role order stays valid.
    - Assistant tool_calls become tool_use blocks; the tool messages after them become tool_result
      blocks, matched by position (Brain appends exactly one result per call, in order).
    - The greeting is the agent's first line, so a short user line is put before it.
    """
    system = ""
    out: list[dict] = []
    pending_ids: list[str] = []
    ids = itertools.count(1)

    def user_blocks() -> list:
        if out and out[-1]["role"] == "user":
            return out[-1]["content"]
        out.append({"role": "user", "content": []})
        return out[-1]["content"]

    for i, m in enumerate(messages):
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            if i == 0 and not system:
                system = content
            elif content:
                user_blocks().append({"type": "text", "text": f"<operator_note>{content}</operator_note>"})
        elif role == "user":
            if content:
                user_blocks().append({"type": "text", "text": content})
        elif role == "assistant":
            blocks: list[dict] = []
            if content.strip():
                blocks.append({"type": "text", "text": content})
            pending_ids = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                tid = tc.get("id") or f"toolu_pv{next(ids)}"
                pending_ids.append(tid)
                blocks.append({"type": "tool_use", "id": tid, "name": fn.get("name", ""), "input": args})
            if not blocks:
                continue  # an empty reply (all sentences blocked) adds nothing
            if not out:
                out.append({"role": "user", "content": [{"type": "text", "text": CALL_CONNECTED}]})
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            tid = pending_ids.pop(0) if pending_ids else f"toolu_pv{next(ids)}"
            user_blocks().append({"type": "tool_result", "tool_use_id": tid, "content": content})
    if not out or out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [{"type": "text", "text": CALL_CONNECTED}]})
    return system, out


class ClaudeBrain(Brain):
    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL, confirm_gate: bool = False,
                 client: anthropic.AsyncAnthropic | None = None) -> None:
        super().__init__(None, "", model, confirm_gate=confirm_gate)  # type: ignore[arg-type]
        self.claude = client or _shared_client()

    async def warm_up(self) -> None:
        """A one token request: checks the key and the model name at startup."""
        try:
            await self.claude.messages.create(
                model=self.model, max_tokens=1, thinking={"type": "disabled"},
                messages=[{"role": "user", "content": "Hi"}])
        except anthropic.APIError as e:
            raise BrainError(_describe(e)) from e
        except TypeError as e:  # the SDK found no credentials at all
            raise BrainError(f"no Claude API key found ({e})") from e

    async def _chat_stream(self, messages: list[dict], tools: list[dict]) -> AsyncIterator[dict]:
        system, claude_messages = to_claude_messages(messages)
        kwargs: dict = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": system + CLAUDE_NOTE,
            "messages": claude_messages,
            "thinking": {"type": "disabled"},  # lowest latency for a spoken turn
            "output_config": {"effort": "low"},
        }
        if tools:
            kwargs["tools"] = to_claude_tools(tools)
        try:
            async with self.claude.messages.stream(**kwargs) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield {"message": {"content": text}}
                final = await stream.get_final_message()
        except anthropic.APIError as e:
            raise BrainError(_describe(e)) from e
        except TypeError as e:  # the SDK found no credentials at all
            raise BrainError("no Claude API key found (set ANTHROPIC_API_KEY)") from e
        if final.stop_reason == "refusal":
            return  # nothing more to say; Brain ends the round with what was spoken
        calls = [
            {"id": b.id, "function": {"name": b.name, "arguments": dict(b.input or {})}}
            for b in final.content if b.type == "tool_use"
        ]
        if calls and final.stop_reason != "max_tokens":  # a cut off tool call is never run
            yield {"message": {"tool_calls": calls}}


_CLIENT: anthropic.AsyncAnthropic | None = None


def _shared_client() -> anthropic.AsyncAnthropic:
    """One client for all calls (keeps connections warm). Credentials: ANTHROPIC_API_KEY or `ant auth login`."""
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.AsyncAnthropic()
    return _CLIENT


def _describe(e: anthropic.APIError) -> str:
    if isinstance(e, anthropic.AuthenticationError):
        return "Claude API rejected the key (check ANTHROPIC_API_KEY)"
    if isinstance(e, anthropic.NotFoundError):
        return f"Claude API: model not found ({e.message})"
    if isinstance(e, anthropic.RateLimitError):
        return "Claude API: rate limited, try again in a moment"
    if isinstance(e, anthropic.APIStatusError):
        return f"Claude API error {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return "Claude API: network error"
    return f"Claude API error: {e}"


def make_brain(http: httpx.AsyncClient, settings) -> Brain:
    if settings.brain == "claude":
        return ClaudeBrain(settings.claude_model, confirm_gate=not settings.bug)
    return Brain(http, settings.ollama_url, settings.model, confirm_gate=not settings.bug)

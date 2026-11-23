"""Project config file `panchovoice.yaml` (contract section 1)."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

from .errors import ConfigError, FieldError, pydantic_errors

CONFIG_NAME = "panchovoice.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMConfig(_Strict):
    provider: Literal["claude-cli", "anthropic", "ollama"] = "claude-cli"
    model: str = "haiku"
    budget_usd: float = 10.0
    max_calls_per_run: int = 60
    # optional, not in the v1 contract example: base url for ollama
    url: str | None = None


ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WHISPER_MODELS = ("tiny", "base", "small", "medium")

# provider -> (url, model, default_voice, api key env var)
TTS_DEFAULTS: dict[str, tuple[str, str | None, str | None, str | None]] = {
    "kokoro": ("http://localhost:8880", None, None, None),
    "elevenlabs": ("https://api.elevenlabs.io", "eleven_multilingual_v2", "JBFqnCBsd6RMkjVDRZzb", "ELEVENLABS_API_KEY"),
    "deepgram": ("https://api.deepgram.com", None, "aura-2-thalia-en", "DEEPGRAM_API_KEY"),
}
STT_DEFAULTS: dict[str, tuple[str | None, str, str | None]] = {
    "faster-whisper": (None, "small", None),
    "deepgram": ("https://api.deepgram.com", "nova-3", "DEEPGRAM_API_KEY"),
}


def _check_env_name(value: str | None) -> None:
    if value is not None and not ENV_NAME.fullmatch(value):
        raise ValueError("'api_key_env' must be an env var name (letters, digits, _), not the key itself")


class TTSConfig(_Strict):
    """The caller voice. Cloud API keys come only from environment variables, never from the yaml."""

    provider: Literal["kokoro", "elevenlabs", "deepgram"] = "kokoro"
    url: str | None = None            # default per provider
    model: str | None = None          # elevenlabs model id (deepgram: the voice is the model)
    default_voice: str | None = None  # provider voice for scenarios that name a Kokoro voice (af_heart, ...)
    voices: dict[str, str] = Field(default_factory=dict)  # scenario voice -> provider voice
    api_key_env: str | None = None    # name of the env var that holds the key

    @model_validator(mode="after")
    def _defaults(self) -> "TTSConfig":
        _check_env_name(self.api_key_env)
        url, model, voice, env = TTS_DEFAULTS[self.provider]
        self.url = self.url or url
        self.model = self.model or model
        self.default_voice = self.default_voice or voice
        self.api_key_env = self.api_key_env or env
        return self

    @property
    def key_env(self) -> str:
        return self.api_key_env or ""

    @property
    def is_cloud(self) -> bool:
        return self.provider != "kokoro"


class STTConfig(_Strict):
    """Speech to text inside the harness. Cloud API keys come only from environment variables."""

    provider: Literal["faster-whisper", "deepgram"] = "faster-whisper"
    model: str | None = None        # whisper: tiny | base | small | medium; deepgram: nova-3, ...
    url: str | None = None          # deepgram only
    api_key_env: str | None = None  # deepgram only

    @model_validator(mode="after")
    def _defaults(self) -> "STTConfig":
        _check_env_name(self.api_key_env)
        url, model, env = STT_DEFAULTS[self.provider]
        self.url = self.url or url
        self.model = self.model or model
        self.api_key_env = self.api_key_env or env
        if self.provider == "faster-whisper" and self.model not in WHISPER_MODELS:
            raise ValueError(f"faster-whisper model must be one of {', '.join(WHISPER_MODELS)}")
        return self

    @property
    def key_env(self) -> str:
        return self.api_key_env or ""

    @property
    def is_cloud(self) -> bool:
        return self.provider != "faster-whisper"


class HttpAuth(_Strict):
    """Auth for an http-turn agent. Secrets come only from environment variables, never from the yaml."""

    type: Literal["none", "bearer", "cookie", "header"] = "none"
    env: str | None = None   # name of the env var that holds the token, the Cookie header, or the header value
    name: str | None = None  # header name, for type: header

    @model_validator(mode="after")
    def _check(self) -> "HttpAuth":
        if self.type != "none" and not self.env:
            raise ValueError(f"auth type '{self.type}' needs 'env': the name of the env var that holds the secret")
        if self.type == "header" and not self.name:
            raise ValueError("auth type 'header' needs 'name': the header name")
        if self.env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.env):
            raise ValueError("auth 'env' must be an env var name (letters, digits, _), not the secret itself")
        return self


class HttpTranscribe(_Strict):
    path: str = "/transcribe"
    mode: Literal["multipart", "json"] = "multipart"  # multipart file upload, or JSON with base64 audio
    field: str = "audio"
    format: Literal["wav"] = "wav"
    response_text: str = "transcript"  # dotted path into the JSON reply
    extra_body: dict[str, Any] = Field(default_factory=dict)


class HttpChat(_Strict):
    path: str = "/chat"
    body: str = "messages"  # key that holds the history [{role, content}]
    response_text: str = "reply"
    response_actions: str | None = "actions"


class HttpSpeak(_Strict):
    path: str = "/speak"
    body_text: str = "text"
    response: Literal["audio", "json"] = "audio"  # raw audio body (mp3 or wav), or JSON with base64 audio
    audio_field: str = "audio"  # for response: json
    extra_body: dict[str, Any] = Field(default_factory=dict)


class HttpConfig(_Strict):
    base_url: str
    auth: HttpAuth = Field(default_factory=HttpAuth)
    transcribe: HttpTranscribe | None = None  # missing: text mode (caller text sent as is, WER not measured)
    chat: HttpChat
    speak: HttpSpeak | None = None  # missing: the agent channel stays silent, latency is text latency
    tools_from: Literal["actions"] | None = None
    extra_body: dict[str, Any] = Field(default_factory=dict)  # merged into the chat body
    max_turns_per_minute: int | None = Field(None, ge=1)
    timeout_s: float = Field(30.0, gt=0)


class ElevenLabsAgentConfig(_Strict):
    """An ElevenLabs Agents (Conversational AI) agent. The API key comes only from an environment variable."""

    agent_id: str
    api_key_env: str = "ELEVENLABS_API_KEY"  # signed url for private agents, and the conversation record after the call
    api_url: str = "https://api.elevenlabs.io"
    environment: str | None = None
    branch_id: str | None = None
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    fetch_conversation: bool = True     # read tool calls, cost and the agent's transcript after the call
    conversation_wait_s: float = 30.0   # how long to wait for the conversation record to be ready

    @model_validator(mode="after")
    def _check(self) -> "ElevenLabsAgentConfig":
        _check_env_name(self.api_key_env)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.agent_id):
            raise ValueError("'agent_id' must be the ElevenLabs agent id (agent_...)")
        return self

    @property
    def ws_url(self) -> str:
        base = self.api_url.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{base}/v1/convai/conversation?agent_id={self.agent_id}"


class AgentConfig(_Strict):
    id: str
    name: str = ""
    adapter: Literal["websocket", "http-turn", "elevenlabs"] = "websocket"
    url: str = ""  # websocket: ws:// url. http-turn: defaults to http.base_url. elevenlabs: set from agent_id
    language: Literal["en", "es"] = "en"
    http: HttpConfig | None = None  # required for adapter: http-turn
    elevenlabs: ElevenLabsAgentConfig | None = None  # required for adapter: elevenlabs

    @model_validator(mode="after")
    def _check(self) -> "AgentConfig":
        if self.adapter == "elevenlabs":
            if self.elevenlabs is None:
                raise ValueError("adapter 'elevenlabs' needs an 'elevenlabs' section with 'agent_id'")
            if not self.url:
                self.url = self.elevenlabs.ws_url
        if self.adapter == "websocket" and not self.url:
            raise ValueError("adapter 'websocket' needs 'url'")
        if self.adapter == "http-turn":
            if self.http is None:
                raise ValueError("adapter 'http-turn' needs an 'http' section (base_url, chat, ...)")
            if not self.url:
                self.url = self.http.base_url
        return self


class GateConfig(_Strict):
    pass_rate: float = Field(0.95, ge=0, le=1)
    critical_must_pass_all: bool = True


def _default_agents() -> list[AgentConfig]:
    return [
        AgentConfig(
            id="acme-demo",
            name="ACME Appointments (demo)",
            adapter="websocket",
            url="ws://127.0.0.1:8710/ws",
            language="en",
        )
    ]


class Config(_Strict):
    version: int = 1
    data_dir: str = "./data"
    scenarios_dir: str = "./scenarios"
    fixtures_dir: str = "./fixtures"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    agents: list[AgentConfig] = Field(default_factory=_default_agents)
    gate: GateConfig = Field(default_factory=GateConfig)

    _root: Path = PrivateAttr(default_factory=Path.cwd)
    _source: Path | None = PrivateAttr(default=None)

    # resolved paths ---------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    @property
    def source(self) -> Path | None:
        """The yaml file this config came from, or None when defaults were used."""
        return self._source

    def _resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (self._root / path).resolve()

    @property
    def data_path(self) -> Path:
        return self._resolve(self.data_dir)

    @property
    def scenarios_path(self) -> Path:
        return self._resolve(self.scenarios_dir)

    @property
    def fixtures_path(self) -> Path:
        return self._resolve(self.fixtures_dir)

    @property
    def runs_path(self) -> Path:
        return self.data_path / "runs"

    @property
    def db_path(self) -> Path:
        return self.data_path / "panchovoice.db"

    def agent(self, agent_id: str) -> AgentConfig | None:
        for a in self.agents:
            if a.id == agent_id:
                return a
        return None

    def public_dict(self) -> dict:
        """The config without secrets (there are none in v1, kept for the API server)."""
        return self.model_dump(mode="json")


def find_config(start: Path | None = None) -> Path | None:
    """Look for panchovoice.yaml in `start` (default cwd) and its parents."""
    env = os.environ.get("PV_CONFIG")
    if env:
        p = Path(env)
        return p if p.exists() else None
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        cand = d / CONFIG_NAME
        if cand.is_file():
            return cand
    return None


def _default_root(start: Path | None = None) -> Path:
    """Without a config file: the nearest folder (cwd or a parent) that has `scenarios/`, else cwd."""
    cur = (start or Path.cwd()).resolve()
    for d in [cur, *cur.parents]:
        if (d / "scenarios").is_dir():
            return d
    return cur


def parse_config_text(text: str, source: str = CONFIG_NAME) -> Config:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError([FieldError("", f"invalid YAML: {exc}")], source) from exc
    if not isinstance(data, dict):
        raise ConfigError([FieldError("", "the file must be a mapping")], source)
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(pydantic_errors(exc), source) from exc


def load_config(path: str | Path | None = None, start: Path | None = None) -> Config:
    """Load panchovoice.yaml. With no file found, return defaults (contract section 1).

    `path` may be a file or a folder that holds panchovoice.yaml.
    """
    file: Path | None
    if path is not None:
        file = Path(path)
        if file.is_dir():
            file = file / CONFIG_NAME
        if not file.is_file():
            raise ConfigError([FieldError("", f"file not found: {file}")], str(file))
    else:
        file = find_config(start)
    if file is None:
        cfg = Config()
        cfg._root = _default_root(start)
        return cfg
    cfg = parse_config_text(file.read_text(encoding="utf-8"), str(file))
    cfg._root = file.resolve().parent
    cfg._source = file.resolve()
    return cfg

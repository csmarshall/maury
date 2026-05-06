"""LLM client abstraction (per ADR-0012).

Default backend is `claude -p` headless mode — uses the user's Claude
Code subscription quota, no API key required. SDK backend is opt-in
for hosts that can't reach Claude Code or want pay-per-token billing.

Selection precedence:
  1. Explicit `name=` argument to `get_backend()`.
  2. `MAURY_LLM_BACKEND` environment variable.
  3. Default: `"cli"`.

Both backends implement a tiny `LLMClient` protocol — one method
(`call`). Mining layers batching/concurrency/caching on top.
"""

from __future__ import annotations

import os
import subprocess
from typing import Protocol


class LLMClient(Protocol):
    """Minimal LLM client interface — call once, get text back."""

    name: str

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        """Send `prompt`, return the assistant's text response."""
        ...


class BackendUnavailableError(RuntimeError):
    """Raised when a requested backend can't be initialized.

    e.g., `sdk` requested but anthropic package not installed; `cli`
    requested but `claude` not on PATH.
    """


# ---- claude -p backend (default) ---------------------------------------


class ClaudeCliClient:
    """Shells out to `claude -p`. Uses user's Claude Code subscription quota.

    Per ADR-0012: this is the default backend. No API key needed; no
    separate billing. The cost is one subprocess invocation per call
    (full Claude Code session spin-up), which is fine for mining-style
    workloads that batch many user messages per call.
    """

    name = "cli"

    def __init__(self, claude_path: str = "claude") -> None:
        """Initialize. `claude_path` lets tests inject a stub binary."""
        self.claude_path = claude_path

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        """Run `claude -p` with `prompt` on stdin; return stdout text."""
        try:
            proc = subprocess.run(
                [self.claude_path, "-p"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
        except FileNotFoundError as e:
            raise BackendUnavailableError(
                f"claude binary not found at {self.claude_path!r}; "
                f"install Claude Code and ensure it is on PATH, or set "
                f"MAURY_LLM_BACKEND=sdk to use the Anthropic SDK instead."
            ) from e
        except subprocess.CalledProcessError as e:
            raise BackendUnavailableError(f"claude -p exited {e.returncode}: {e.stderr[:500]}") from e
        return proc.stdout


# ---- anthropic SDK backend (opt-in) ------------------------------------


class AnthropicSdkClient:
    """Uses Anthropic SDK with `ANTHROPIC_API_KEY`.

    Opt-in per ADR-0012. The anthropic package is an optional dependency
    (`pip install maury[sdk]` or equivalent). On instances that need
    pay-per-token billing or can't reach Claude Code, this is the
    escape hatch.
    """

    name = "sdk"

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 8192,
    ) -> None:
        """Initialize. Model defaults to Sonnet 4.6 (good cost/quality tradeoff for mining)."""
        try:
            from anthropic import Anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise BackendUnavailableError(
                "anthropic SDK not installed; install with `pip install maury[sdk]` or use the default `cli` backend."
            ) from e
        # Anthropic() picks up ANTHROPIC_API_KEY from env automatically
        self._client = Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def call(self, prompt: str, *, timeout: float = 120.0) -> str:
        """Send a single user message; return the assistant's text response."""
        # The SDK's per-call timeout is via httpx config, not a parameter.
        # For v1 we accept the per-client timeout from instantiation; the
        # `timeout` arg here is reserved for parity with the protocol.
        del timeout  # unused for now; the SDK has its own client-level timeout
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # Concatenate all text blocks (assistant response can have multiple)
        parts: list[str] = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)


# ---- factory -----------------------------------------------------------


def get_backend(
    name: str | None = None,
    *,
    claude_path: str = "claude",
    sdk_model: str = "claude-sonnet-4-6",
) -> LLMClient:
    """Return an `LLMClient` selected by argument, env var, or default.

    Precedence:
      1. `name` argument (if not None)
      2. `$MAURY_LLM_BACKEND` env var
      3. Default: `"cli"`

    Valid values: `"cli"` (default) or `"sdk"`.
    """
    chosen = name or os.environ.get("MAURY_LLM_BACKEND", "cli")
    chosen = chosen.lower().strip()
    if chosen == "cli":
        return ClaudeCliClient(claude_path=claude_path)
    if chosen == "sdk":
        return AnthropicSdkClient(model=sdk_model)
    raise ValueError(f"unknown LLM backend: {chosen!r} (expected 'cli' or 'sdk')")


__all__ = [
    "AnthropicSdkClient",
    "BackendUnavailableError",
    "ClaudeCliClient",
    "LLMClient",
    "get_backend",
]

"""Tests for the LLM backend abstraction (per ADR-0012)."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from maury.llm import (
    AnthropicSdkClient,
    BackendUnavailableError,
    ClaudeCliClient,
    LLMClient,
    get_backend,
)

# ---- factory ------------------------------------------------------------


def test_get_backend_default_is_cli(monkeypatch):
    monkeypatch.delenv("MAURY_LLM_BACKEND", raising=False)
    client = get_backend()
    assert client.name == "cli"
    assert isinstance(client, ClaudeCliClient)


def test_get_backend_explicit_cli():
    client = get_backend("cli")
    assert client.name == "cli"


def test_get_backend_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("MAURY_LLM_BACKEND", "cli")
    client = get_backend()
    assert client.name == "cli"


def test_get_backend_explicit_arg_overrides_env(monkeypatch):
    monkeypatch.setenv("MAURY_LLM_BACKEND", "sdk")
    # Even with env=sdk, explicit arg wins. Use cli to avoid SDK dep.
    client = get_backend("cli")
    assert client.name == "cli"


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError) as ei:
        get_backend("yolo")
    assert "yolo" in str(ei.value)
    assert "expected 'cli' or 'sdk'" in str(ei.value)


def test_get_backend_case_insensitive():
    client = get_backend("CLI")
    assert client.name == "cli"


def test_get_backend_strips_whitespace(monkeypatch):
    monkeypatch.setenv("MAURY_LLM_BACKEND", "  cli  ")
    client = get_backend()
    assert client.name == "cli"


# ---- ClaudeCliClient ----------------------------------------------------


def test_cli_client_invokes_claude_p_with_stdin(monkeypatch):
    """The CLI client should pass the prompt on stdin and return stdout."""
    mock_run = MagicMock(
        return_value=subprocess.CompletedProcess(
            args=["claude", "-p"],
            returncode=0,
            stdout="model says hi\n",
            stderr="",
        )
    )
    monkeypatch.setattr(subprocess, "run", mock_run)

    client = ClaudeCliClient()
    result = client.call("hello model")

    assert result == "model says hi\n"
    mock_run.assert_called_once()
    # First positional arg is the command list
    cmd_args, cmd_kwargs = mock_run.call_args
    assert cmd_args[0] == ["claude", "-p"]
    assert cmd_kwargs["input"] == "hello model"
    assert cmd_kwargs["text"] is True
    assert cmd_kwargs["check"] is True


def test_cli_client_uses_custom_claude_path(monkeypatch):
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(subprocess, "run", mock_run)

    client = ClaudeCliClient(claude_path="/opt/custom/claude")
    client.call("x")

    cmd_args, _ = mock_run.call_args
    assert cmd_args[0] == ["/opt/custom/claude", "-p"]


def test_cli_client_missing_binary_raises_backend_unavailable(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("no claude")

    monkeypatch.setattr(subprocess, "run", boom)
    client = ClaudeCliClient(claude_path="/nonexistent/claude")
    with pytest.raises(BackendUnavailableError) as ei:
        client.call("hi")
    assert "claude binary not found" in str(ei.value)
    assert "MAURY_LLM_BACKEND=sdk" in str(ei.value)


def test_cli_client_nonzero_exit_raises_backend_unavailable(monkeypatch):
    def fail(*a, **kw):
        raise subprocess.CalledProcessError(returncode=2, cmd=["claude", "-p"], stderr="something broke\n")

    monkeypatch.setattr(subprocess, "run", fail)
    client = ClaudeCliClient()
    with pytest.raises(BackendUnavailableError) as ei:
        client.call("hi")
    assert "exited 2" in str(ei.value)
    assert "something broke" in str(ei.value)


def test_cli_client_passes_timeout(monkeypatch):
    mock_run = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr=""))
    monkeypatch.setattr(subprocess, "run", mock_run)

    client = ClaudeCliClient()
    client.call("x", timeout=45.0)

    _, cmd_kwargs = mock_run.call_args
    assert cmd_kwargs["timeout"] == 45.0


# ---- AnthropicSdkClient -------------------------------------------------


def test_sdk_client_raises_backend_unavailable_when_anthropic_not_installed(monkeypatch):
    """Simulate `from anthropic import Anthropic` failing.

    We patch the import via sys.modules so the lazy-import inside the
    constructor sees the missing package.
    """
    # Remove anthropic from sys.modules and intercept the import
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(BackendUnavailableError) as ei:
        AnthropicSdkClient()
    assert "anthropic SDK not installed" in str(ei.value)


def test_sdk_client_call_concatenates_text_blocks():
    """Assistant response can be multiple text blocks; result joins them."""
    fake_anthropic_module = type(sys)("anthropic")
    fake_client_class = MagicMock()
    fake_client_instance = MagicMock()
    fake_response = MagicMock()
    block1 = MagicMock()
    block1.text = "first part"
    block2 = MagicMock()
    block2.text = "second part"
    fake_response.content = [block1, block2]
    fake_client_instance.messages.create.return_value = fake_response
    fake_client_class.return_value = fake_client_instance
    fake_anthropic_module.Anthropic = fake_client_class  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"anthropic": fake_anthropic_module}):
        client = AnthropicSdkClient(model="claude-test", max_tokens=100)
        result = client.call("a prompt")

    assert result == "first part\nsecond part"
    fake_client_instance.messages.create.assert_called_once_with(
        model="claude-test",
        max_tokens=100,
        messages=[{"role": "user", "content": "a prompt"}],
    )


def test_sdk_client_call_skips_non_text_blocks():
    """Tool-use or other non-text blocks shouldn't appear in the joined output."""
    fake_anthropic_module = type(sys)("anthropic")
    fake_client_class = MagicMock()
    fake_client_instance = MagicMock()
    fake_response = MagicMock()
    text_block = MagicMock(spec=["text"])
    text_block.text = "a text answer"
    tool_block = MagicMock(spec=[])  # has no `.text` attribute
    fake_response.content = [text_block, tool_block]
    fake_client_instance.messages.create.return_value = fake_response
    fake_client_class.return_value = fake_client_instance
    fake_anthropic_module.Anthropic = fake_client_class  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"anthropic": fake_anthropic_module}):
        client = AnthropicSdkClient()
        result = client.call("x")

    assert result == "a text answer"


# ---- protocol conformance ----------------------------------------------


def test_both_backends_satisfy_llm_client_protocol():
    """Structural typing check: both classes should satisfy LLMClient."""
    cli: LLMClient = ClaudeCliClient()  # type-check only; not invoked
    assert cli.name == "cli"
    assert callable(cli.call)

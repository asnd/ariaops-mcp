"""
Extensive tests for user-input validation functions in test-ui/app.py.

Covers: JWT handling, token management, proxy status, model discovery,
AriaOps credential validation, and chat input guards.
"""

import base64
import json
import socket
import time
from typing import Any, cast
from unittest.mock import MagicMock, patch

import app
import pytest
from app import (
    HOSTED_PROVIDERS,
    _agentic_loop,
    _build_auth_url,
    _find_free_port,
    _jwt_exp,
    _make_azure_callback_handler,
    _new_session_state,
    _OAuthFlow,
    _start_callback_server,
    _token_valid,
    _validate_oauth_callback,
    apply_manual_token,
    chat_fn,
    fetch_models,
    fetch_token_stream,
    get_llm_token,
    init_ariaops,
    proxy_status_text,
    token_status_text,
)

# ─── helpers ───────────────────────────────────────────────────────────────────


def _make_jwt(exp: int, extra: dict | None = None) -> str:
    """Build a syntactically valid (unsigned) JWT with the given exp."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    claims = {"sub": "test-user", "exp": exp}
    if extra:
        claims.update(extra)
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def _future_jwt(minutes: int = 60) -> str:
    return _make_jwt(int(time.time()) + minutes * 60)


def _expired_jwt(minutes: int = 5) -> str:
    return _make_jwt(int(time.time()) - minutes * 60)


def _model_entry(
    alias: str,
    mode: str = "chat",
    litellm_model: str = "hosted_vllm/llama3",
) -> dict:
    """Build a /model/info data entry with the given provider/model string."""
    return {
        "model_name": alias,
        "model_info": {"mode": mode},
        "litellm_params": {"model": litellm_model},
    }


# ─── _jwt_exp ──────────────────────────────────────────────────────────────────


class TestJwtExp:
    def test_valid_token_returns_exp(self):
        exp = int(time.time()) + 3600
        tok = _make_jwt(exp)
        assert _jwt_exp(tok) == exp

    def test_missing_exp_returns_zero(self):
        # exp=0 is technically present; test a token with no exp field at all
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b'{"sub":"x"}').rstrip(b"=").decode()
        no_exp_tok = f"{header}.{payload}.sig"
        assert _jwt_exp(no_exp_tok) == 0

    def test_malformed_token_returns_zero(self):
        assert _jwt_exp("not.a.jwt") == 0
        assert _jwt_exp("garbage") == 0
        assert _jwt_exp("") == 0

    def test_extra_padding_handled(self):
        """Payloads whose base64 length is not a multiple of 4."""
        exp = int(time.time()) + 1800
        tok = _make_jwt(exp)
        # Remove the padding we added — the function should re-add it
        assert _jwt_exp(tok) == exp

    def test_two_segment_token_returns_zero(self):
        assert _jwt_exp("only.twoparts") == 0


# ─── _token_valid ──────────────────────────────────────────────────────────────


class TestTokenValid:
    def test_future_token_is_valid(self):
        assert _token_valid(_future_jwt()) is True

    def test_expired_token_is_invalid(self):
        assert _token_valid(_expired_jwt()) is False

    def test_none_is_invalid(self):
        assert _token_valid(None) is False

    def test_empty_string_is_invalid(self):
        assert _token_valid("") is False

    def test_slack_respected(self):
        # Token expires in 30 s but slack=60 → invalid
        tok = _make_jwt(int(time.time()) + 30)
        assert _token_valid(tok, slack=60) is False
        # Same token with slack=10 → valid
        assert _token_valid(tok, slack=10) is True

    def test_garbage_string_is_invalid(self):
        assert _token_valid("thisisnot.a.jwttoken") is False


# ─── apply_manual_token ────────────────────────────────────────────────────────


class TestApplyManualToken:
    def test_empty_input_rejected(self):
        result = apply_manual_token("")
        assert result == "No token provided."

    def test_whitespace_only_rejected(self):
        result = apply_manual_token("   \n  ")
        assert result == "No token provided."

    def test_expired_token_rejected(self):
        tok = _expired_jwt()
        result = apply_manual_token(tok)
        assert result == "Token is already expired."

    def test_valid_token_accepted(self):
        tok = _future_jwt(90)
        result = apply_manual_token(tok)
        assert "Valid" in result
        assert app._token_cache["value"] == tok

    def test_valid_token_sets_env(self, monkeypatch):
        tok = _future_jwt(90)
        apply_manual_token(tok)
        import os

        assert os.environ.get("LITELLM_TOKEN") == tok

    def test_leading_trailing_whitespace_stripped(self):
        tok = _future_jwt(90)
        result = apply_manual_token(f"  {tok}  ")
        assert "Valid" in result
        assert app._token_cache["value"] == tok

    def test_token_with_zero_exp_rejected(self):
        tok = _make_jwt(0)
        result = apply_manual_token(tok)
        assert result == "Token is missing a valid expiration claim."

    def test_malformed_token_rejected(self):
        assert apply_manual_token("not-a-jwt") == "Token is not a valid JWT."

    def test_session_token_is_isolated(self):
        session_a = _new_session_state()
        session_b = _new_session_state()
        tok = _future_jwt(90)
        result = apply_manual_token(tok, session_a)
        assert "Valid" in result
        assert session_a["llm_token"]["value"] == tok
        assert session_b["llm_token"]["value"] is None
        assert app._token_cache["value"] is None


# ─── token_status_text ────────────────────────────────────────────────────────


class TestTokenStatusText:
    def test_no_token_returns_no_token_set(self):
        assert token_status_text() == "No token set"

    def test_expired_cache_returns_expired(self):
        app._token_cache.update({"value": _expired_jwt(), "exp": 0})
        result = token_status_text()
        assert "expired" in result.lower() or "invalid" in result.lower()

    def test_valid_cache_returns_valid(self):
        tok = _future_jwt(120)
        app._token_cache.update({"value": tok, "exp": _jwt_exp(tok)})
        result = token_status_text()
        assert "Valid" in result
        assert "min remaining" in result

    def test_valid_env_token_shows_status(self, monkeypatch):
        tok = _future_jwt(60)
        monkeypatch.setenv("LITELLM_TOKEN", tok)
        result = token_status_text()
        assert "Valid" in result


# ─── get_llm_token ────────────────────────────────────────────────────────────


class TestGetLlmToken:
    def test_raises_when_no_token(self):
        with pytest.raises(RuntimeError, match="No valid token"):
            get_llm_token()

    def test_returns_env_token_when_valid(self, monkeypatch):
        tok = _future_jwt(60)
        monkeypatch.setenv("LITELLM_TOKEN", tok)
        assert get_llm_token() == tok

    def test_prefers_env_over_cache(self, monkeypatch):
        cached = _future_jwt(30)
        env_tok = _future_jwt(120)
        app._token_cache.update({"value": cached, "exp": _jwt_exp(cached)})
        monkeypatch.setenv("LITELLM_TOKEN", env_tok)
        assert get_llm_token() == env_tok

    def test_falls_back_to_cache(self):
        tok = _future_jwt(60)
        app._token_cache.update({"value": tok, "exp": _jwt_exp(tok)})
        assert get_llm_token() == tok

    def test_expired_env_token_falls_back_to_cache(self, monkeypatch):
        env_tok = _expired_jwt()
        cache_tok = _future_jwt(60)
        monkeypatch.setenv("LITELLM_TOKEN", env_tok)
        app._token_cache.update({"value": cache_tok, "exp": _jwt_exp(cache_tok)})
        assert get_llm_token() == cache_tok

    def test_expired_env_and_cache_raises(self, monkeypatch):
        monkeypatch.setenv("LITELLM_TOKEN", _expired_jwt())
        app._token_cache.update({"value": _expired_jwt(), "exp": 0})
        with pytest.raises(RuntimeError):
            get_llm_token()

    def test_session_state_isolated_from_env(self, monkeypatch):
        monkeypatch.setenv("LITELLM_TOKEN", _future_jwt(60))
        session = _new_session_state()
        session["llm_token"].update({"value": _future_jwt(10), "exp": _jwt_exp(_future_jwt(10))})
        assert get_llm_token(session) == session["llm_token"]["value"]


# ─── proxy_status_text ────────────────────────────────────────────────────────


class TestProxyStatusText:
    def test_no_proxy_shows_direct(self):
        result = proxy_status_text()
        assert "direct" in result.lower()
        assert "No proxy configured" in result

    def test_https_proxy_shown(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp:3128")
        result = proxy_status_text()
        assert "HTTPS=http://proxy.corp:3128" in result
        assert "direct (bypassed)" in result  # LLM GW still direct

    def test_http_proxy_shown_when_no_https(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp:3128")
        result = proxy_status_text()
        assert "HTTP=http://proxy.corp:3128" in result

    def test_lowercase_env_vars_recognized(self, monkeypatch):
        monkeypatch.setenv("https_proxy", "http://lc-proxy:8080")
        result = proxy_status_text()
        assert "HTTPS=http://lc-proxy:8080" in result

    def test_no_proxy_included_when_set(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
        monkeypatch.setenv("NO_PROXY", "localhost,10.0.0.0/8")
        result = proxy_status_text()
        assert "NO_PROXY=localhost,10.0.0.0/8" in result

    def test_https_takes_precedence_over_http(self, monkeypatch):
        monkeypatch.setenv("HTTP_PROXY", "http://http-proxy:3128")
        monkeypatch.setenv("HTTPS_PROXY", "http://https-proxy:3128")
        result = proxy_status_text()
        assert "HTTPS=http://https-proxy:3128" in result
        assert "HTTP=http://http-proxy:3128" not in result


# ─── fetch_models ─────────────────────────────────────────────────────────────


class TestFetchModels:
    """Tests for URL validation and model filtering logic."""

    def test_empty_url_returns_error(self):
        models, err = fetch_models("")
        assert models == []
        assert err is not None
        assert "URL not set" in err

    def test_none_url_returns_error(self):
        models, err = fetch_models(None)  # type: ignore[arg-type]
        assert models == []
        assert err

    def test_non_http_url_rejected(self):
        models, err = fetch_models("ftp://example.com")
        assert models == []
        assert err is not None
        assert "URL not set" in err

    def test_relative_path_rejected(self):
        models, err = fetch_models("/model/info")
        assert models == []
        assert err

    def test_no_valid_token_returns_error(self):
        # No token in cache/env → get_llm_token() raises
        models, err = fetch_models("http://gw.example.com")
        assert models == []
        assert err  # the RuntimeError message propagates

    def _mock_get(self, data: list, status: int = 200):
        """Return a mock SESSION.get context."""
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = {"data": data}
        return patch.object(app.SESSION, "get", return_value=resp)

    def _patch_token(self, tok: str | None = None):
        """Patch get_llm_token to return a dummy token."""
        return patch("app.get_llm_token", return_value=tok or "dummy-token")

    def test_hosted_vllm_models_included(self):
        data = [_model_entry("llama3-70b", litellm_model="hosted_vllm/meta/llama3")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert err is None
        assert "llama3-70b" in models

    def test_gpt_oss_models_included(self):
        data = [_model_entry("gpt4-oss", litellm_model="gpt-oss/some/model")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert err is None
        assert "gpt4-oss" in models

    def test_claude_models_excluded(self):
        data = [_model_entry("claude-3-opus", litellm_model="anthropic/claude-3-opus")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == []
        assert err  # no hosted models found

    def test_openai_models_excluded(self):
        data = [_model_entry("gpt-4o", litellm_model="openai/gpt-4o")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == []

    def test_no_provider_prefix_excluded(self):
        """litellm_model without '/' has empty provider string — excluded."""
        data = [_model_entry("plain-model", litellm_model="llama3")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == []

    def test_non_chat_mode_excluded(self):
        data = [_model_entry("embed-model", mode="embedding", litellm_model="hosted_vllm/embed")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == []

    def test_responses_mode_included(self):
        """'responses' mode is also a valid chat mode."""
        data = [_model_entry("resp-model", mode="responses", litellm_model="hosted_vllm/resp")]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert "resp-model" in models

    def test_duplicate_aliases_deduplicated(self):
        data = [
            _model_entry("llama3", litellm_model="hosted_vllm/a"),
            _model_entry("llama3", litellm_model="hosted_vllm/b"),
        ]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models.count("llama3") == 1

    def test_mixed_providers_filtered_correctly(self):
        data = [
            _model_entry("llama3-hosted", litellm_model="hosted_vllm/llama"),
            _model_entry("gpt-oss-model", litellm_model="gpt-oss/model"),
            _model_entry("claude-skip", litellm_model="anthropic/claude"),
            _model_entry("openai-skip", litellm_model="openai/gpt-4"),
        ]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert err is None
        assert "llama3-hosted" in models
        assert "gpt-oss-model" in models
        assert "claude-skip" not in models
        assert "openai-skip" not in models

    def test_result_is_sorted(self):
        data = [
            _model_entry("zzz-model", litellm_model="hosted_vllm/z"),
            _model_entry("aaa-model", litellm_model="hosted_vllm/a"),
        ]
        with self._mock_get(data), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == sorted(models)

    def test_http_error_returns_message(self):
        with self._mock_get([], status=401), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == []
        assert err is not None
        assert "401" in err

    def test_trailing_slash_stripped(self):
        """URL with trailing slash should still work (rstrip applied)."""
        data = [_model_entry("llama3", litellm_model="hosted_vllm/llama")]
        captured_urls = []

        def fake_get(url, **kwargs):
            captured_urls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"data": data}
            return resp

        with patch.object(app.SESSION, "get", side_effect=fake_get), self._patch_token():
            fetch_models("http://gw.example.com/")

        assert captured_urls[0] == "http://gw.example.com/model/info"

    def test_empty_data_array_returns_error(self):
        with self._mock_get([]), self._patch_token():
            models, err = fetch_models("http://gw.example.com")
        assert models == []
        assert err


# ─── init_ariaops ─────────────────────────────────────────────────────────────


class TestInitAriaops:
    @pytest.mark.asyncio
    async def test_empty_host_rejected(self):
        result = await init_ariaops("", "user", "pass")
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_host_rejected(self):
        result = await init_ariaops("   ", "user", "pass")
        assert "required" in result.lower()

    @pytest.mark.asyncio
    async def test_import_failure_returns_error(self, monkeypatch):
        """If ariaops_mcp is broken, returns an error string instead of raising."""
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "ariaops_mcp.config":
                raise ImportError("simulated import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = await init_ariaops("vrops.example.com", "admin", "secret")
        assert "Failed to connect" in result or "required" in result

    @pytest.mark.asyncio
    async def test_session_state_updated_without_globals(self):
        session = _new_session_state()
        fake_tool = MagicMock(name="tool")
        fake_tool.name = "demo_tool"
        fake_tool.description = "demo"
        fake_tool.inputSchema = {"type": "object"}
        with (
            patch("ariaops_mcp.server._TOOL_DEFS", [fake_tool]),
            patch("ariaops_mcp.server._TOOL_HANDLERS", {"demo_tool": MagicMock()}),
        ):
            result = await init_ariaops("vrops.example.com", "admin", "secret", session_state=session)
        assert "Connected" in result
        assert session["ariaops"]["ready"] is True
        assert app._ariaops_ready is False


# ─── chat_fn ──────────────────────────────────────────────────────────────────


class TestChatFn:
    @pytest.mark.asyncio
    async def test_empty_message_is_no_op(self):
        history = []
        out_chatbot, out_state, cleared, _ = await chat_fn(
            "", history, "sys", "http://gw.example.com", "llama3", 2048, False
        )
        assert out_chatbot == history
        assert cleared == ""

    @pytest.mark.asyncio
    async def test_whitespace_message_is_no_op(self):
        history = []
        out_chatbot, out_state, cleared, _ = await chat_fn(
            "   ", history, "sys", "http://gw.example.com", "llama3", 2048, False
        )
        assert out_chatbot == history

    @pytest.mark.asyncio
    async def test_missing_gateway_url_returns_error(self):
        _, _, _ = None, None, None
        out_chatbot, _, cleared, _ = await chat_fn("hello", [], "sys", "", "llama3", 2048, False)
        assert cleared == ""
        assert any(
            "gateway" in m["content"].lower() or "url" in m["content"].lower()
            for m in out_chatbot
            if m["role"] == "assistant"
        )

    @pytest.mark.asyncio
    async def test_whitespace_gateway_url_returns_error(self):
        out_chatbot, _, _, _ = await chat_fn("hello", [], "sys", "  ", "llama3", 2048, False)
        assert any("gateway" in m["content"].lower() for m in out_chatbot if m["role"] == "assistant")

    @pytest.mark.asyncio
    async def test_missing_model_returns_error(self):
        out_chatbot, _, cleared, _ = await chat_fn("hello", [], "sys", "http://gw.example.com", "", 2048, False)
        assert cleared == ""
        assert any("model" in m["content"].lower() for m in out_chatbot if m["role"] == "assistant")

    @pytest.mark.asyncio
    async def test_none_model_returns_error(self):
        out_chatbot, _, _, _ = await chat_fn("hello", [], "sys", "http://gw.example.com", cast(Any, None), 2048, False)
        assert any("model" in m["content"].lower() for m in out_chatbot if m["role"] == "assistant")

    @pytest.mark.asyncio
    async def test_error_response_includes_user_message(self):
        """Even on error, user message appears in history."""
        out_chatbot, _, _, _ = await chat_fn("test message", [], "sys", "", "llama3", 2048, False)
        assert any(m["role"] == "user" and m["content"] == "test message" for m in out_chatbot)

    @pytest.mark.asyncio
    async def test_input_cleared_on_error(self):
        _, _, cleared, _ = await chat_fn("hello", [], "sys", "", "llama3", 2048, False)
        assert cleared == ""


class TestOAuthFlow:
    def test_validate_oauth_callback_accepts_matching_state_and_nonce(self):
        flow = _OAuthFlow(state="abc", nonce="nonce-1")
        token = _make_jwt(int(time.time()) + 3600, {"nonce": "nonce-1"})
        ok, result = _validate_oauth_callback(token, "abc", flow)
        assert ok is True
        assert result == token

    def test_validate_oauth_callback_rejects_state_mismatch(self):
        flow = _OAuthFlow(state="abc", nonce="nonce-1")
        token = _make_jwt(int(time.time()) + 3600, {"nonce": "nonce-1"})
        ok, result = _validate_oauth_callback(token, "wrong", flow)
        assert ok is False
        assert result == "ERROR:state_mismatch"

    def test_validate_oauth_callback_rejects_nonce_mismatch(self):
        flow = _OAuthFlow(state="abc", nonce="nonce-1")
        token = _make_jwt(int(time.time()) + 3600, {"nonce": "nonce-2"})
        ok, result = _validate_oauth_callback(token, "abc", flow)
        assert ok is False
        assert result == "ERROR:nonce_mismatch"

    def test_start_callback_server_uses_localhost(self):
        flow = _OAuthFlow(state="abc", nonce="nonce-1")
        server, port = _start_callback_server(flow)
        try:
            assert server.server_address[0] == app._AZURE_CALLBACK_HOST
            assert port in app._AZURE_CALLBACK_PORTS
        finally:
            server.server_close()

    @pytest.mark.asyncio
    async def test_fetch_token_stream_bind_failure_returns_message(self):
        with patch("app._start_callback_server", side_effect=OSError("busy")):
            gen = fetch_token_stream(_new_session_state())
            first = await anext(gen)
        assert "No free callback port" in first

    def test_callback_handler_rejects_state_mismatch(self):
        flow = _OAuthFlow(state="good", nonce="nonce-1")
        handler_cls = _make_azure_callback_handler(flow)
        handler = handler_cls.__new__(handler_cls)
        handler.path = "/callback?id_token=abc&state=bad"
        handler.server = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        handler._html = MagicMock()
        handler.do_GET()
        assert flow.result == "ERROR:state_mismatch"


class TestAgenticLoop:
    @pytest.mark.asyncio
    async def test_executes_tool_calls_even_when_finish_reason_stop(self):
        session = _new_session_state()
        session["ariaops"]["ready"] = True
        session["ariaops"]["tools"] = [{"type": "function", "function": {"name": "demo", "parameters": {}}}]

        async def demo_handler(arguments):
            return json.dumps({"ok": True, "args": arguments})

        session["ariaops"]["handlers"] = {"demo": demo_handler}
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"id": "1", "function": {"name": "demo", "arguments": '{"x": 1}'}}],
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
            {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]},
        ]
        with patch("app._chat_completion", side_effect=responses):
            final_text, tool_md = await _agentic_loop("http://gw", "model", [], 10, True, session)
        assert final_text == "done"
        assert "demo" in tool_md

    @pytest.mark.asyncio
    async def test_malformed_tool_arguments_fall_back_to_empty_dict(self):
        session = _new_session_state()
        session["ariaops"]["ready"] = True
        session["ariaops"]["tools"] = [{"type": "function", "function": {"name": "demo", "parameters": {}}}]
        seen = {}

        async def demo_handler(arguments):
            seen.update(arguments)
            return json.dumps({"ok": True})

        session["ariaops"]["handlers"] = {"demo": demo_handler}
        responses = [
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [{"id": "1", "function": {"name": "demo", "arguments": "{"}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]},
        ]
        with patch("app._chat_completion", side_effect=responses):
            final_text, _ = await _agentic_loop("http://gw", "model", [], 10, False, session)
        assert final_text == "done"
        assert seen == {}


# ─── _build_auth_url ──────────────────────────────────────────────────────────


class TestBuildAuthUrl:
    def test_contains_tenant(self):
        url = _build_auth_url(9999)
        assert "05764a73-8c6f-4538-83cd-413f1e1b5665" in url

    def test_contains_client_id(self):
        url = _build_auth_url(9999)
        assert "8bce86e2-607e-402d-87eb-4cff2463e6f7" in url

    def test_redirect_uri_uses_given_port(self):
        url = _build_auth_url(8888)
        assert "localhost%3A8888" in url or "localhost:8888" in url

    def test_response_type_is_id_token(self):
        url = _build_auth_url(9999)
        assert "response_type=id_token" in url

    def test_response_mode_is_fragment(self):
        url = _build_auth_url(9999)
        assert "response_mode=fragment" in url

    def test_contains_nonce(self):
        url = _build_auth_url(9999)
        assert "nonce=" in url

    def test_contains_state(self):
        url = _build_auth_url(9999)
        assert "state=" in url

    def test_different_calls_produce_different_state(self):
        """state and nonce are random per call."""
        url1 = _build_auth_url(9999)
        url2 = _build_auth_url(9999)
        # Extract state values
        from urllib.parse import parse_qs, urlparse

        qs1 = parse_qs(urlparse(url1).query)
        qs2 = parse_qs(urlparse(url2).query)
        assert qs1["state"] != qs2["state"]

    def test_callback_path_present(self):
        url = _build_auth_url(7777)
        # /callback appears URL-encoded inside the redirect_uri query param
        assert "%2Fcallback" in url or "/callback" in url


# ─── _find_free_port ──────────────────────────────────────────────────────────


class TestFindFreePort:
    def test_returns_a_port_from_preferred_list(self):
        port = _find_free_port()
        # May return None if all ports are in use, but in a clean test env one should be free
        if port is not None:
            assert port in app._AZURE_CALLBACK_PORTS

    def test_skips_in_use_ports(self):
        """Occupy one port; _find_free_port should return a different one."""
        occupied = app._AZURE_CALLBACK_PORTS[0]
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((app._AZURE_CALLBACK_HOST, occupied))
            s.listen(1)
            port = _find_free_port()
            if port is not None:
                assert port != occupied
        except OSError:
            pytest.skip(f"Port {occupied} already in use by another process")
        finally:
            s.close()

    def test_returns_none_when_all_ports_taken(self, monkeypatch):
        """If all candidate ports are occupied, returns None."""

        def always_raise(*args, **kwargs):
            raise OSError("address in use")

        # Patch socket.socket to simulate all ports busy
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.bind.side_effect = OSError("address in use")

        with patch("app.socket.socket", return_value=mock_sock):
            port = _find_free_port()
        assert port is None


# ─── HOSTED_PROVIDERS constant ────────────────────────────────────────────────


class TestHostedProviders:
    def test_hosted_vllm_is_included(self):
        assert "hosted_vllm" in HOSTED_PROVIDERS

    def test_gpt_oss_is_included(self):
        assert "gpt-oss" in HOSTED_PROVIDERS

    def test_anthropic_not_included(self):
        assert "anthropic" not in HOSTED_PROVIDERS

    def test_openai_not_included(self):
        assert "openai" not in HOSTED_PROVIDERS

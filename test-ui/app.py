#!/usr/bin/env python3
"""
AriaOps MCP Chatbot — Gradio UI that connects to the AriaOps MCP server
and routes chat through an LLM gateway with tool-use support.

Usage:
    python app.py
    LITELLM_BASE_URL=https://... LITELLM_TOKEN=<jwt> python app.py
    python app.py --port 7861
"""

import argparse
import asyncio
import base64
import binascii
import contextlib
import datetime
import json
import os
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import gradio as gr
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings()

# ─── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
REPO_SRC = SCRIPT_DIR.parent / "src"
sys.path.insert(0, str(REPO_SRC))

# ─── constants ─────────────────────────────────────────────────────────────────
DEFAULT_GATEWAY_URL = os.environ.get("LITELLM_BASE_URL", "")
# Preferred model names, checked in order against whatever the gateway returns
PREFERRED_MODELS = ["nemotron-3-super-120b", "nemotron", "llama", "mistral"]
CHAT_MODES = {"chat", "responses"}
# Only show models hosted on internal infrastructure (hosted_vllm = vLLM, gpt-oss = OSS via OpenAI-compat)
HOSTED_PROVIDERS = {"hosted_vllm", "gpt-oss"}
MAX_TOOL_ROUNDS = 10

SYSTEM_PROMPT_DEFAULT = (
    "You are an AI assistant with access to VMware Aria Operations (vROps). "
    "Use the available tools to answer questions about the infrastructure: "
    "resources, virtual machines, alerts, metrics, capacity, and reports. "
    "When asked about the environment, proactively query the relevant tools. "
    "Always summarize findings clearly and concisely."
)


# ─── HTTP session (LLM GW only — proxy intentionally disabled) ─────────────────
def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    # LLM gateway must be reached directly — disable proxy env-var pickup
    s.trust_env = False
    return s


SESSION = _make_session()


def proxy_status_text() -> str:
    """Return a human-readable summary of proxy env vars (informational only)."""
    hp = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    hsp = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    np = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if not hp and not hsp:
        return "No proxy configured — LLM GW: direct  |  Azure AD: direct (browser)"
    parts = []
    if hsp:
        parts.append(f"HTTPS={hsp}")
    elif hp:
        parts.append(f"HTTP={hp}")
    if np:
        parts.append(f"NO_PROXY={np}")
    return f"Proxy: {', '.join(parts)}  |  LLM GW: direct (bypassed)  |  Azure AD: via browser"


# ─── JWT / token management ────────────────────────────────────────────────────
_token_cache: dict = {"value": None, "exp": 0}
_ariaops_ready = False
_mcp_tools: list[dict] = []
_mcp_handlers: dict = {}


def _new_session_state() -> dict[str, Any]:
    seed_token = os.environ.get("LITELLM_TOKEN", "").strip()
    token_cache = {"value": None, "exp": 0}
    if _token_valid(seed_token, slack=0):
        token_cache = {"value": seed_token, "exp": _jwt_exp(seed_token)}
    return {
        "llm_token": token_cache,
        "ariaops": {
            "ready": False,
            "tools": [],
            "handlers": {},
            "client": None,
            "settings": None,
        },
    }


_GLOBAL_SESSION_STATE = {
    "llm_token": _token_cache,
    "ariaops": {
        "ready": _ariaops_ready,
        "tools": _mcp_tools,
        "handlers": _mcp_handlers,
        "client": None,
        "settings": None,
    },
}


def _ensure_session_state(session_state: dict[str, Any] | None) -> dict[str, Any]:
    if session_state is None:
        return _GLOBAL_SESSION_STATE
    if not isinstance(session_state, dict):
        return _new_session_state()

    if "llm_token" not in session_state or not isinstance(session_state["llm_token"], dict):
        session_state["llm_token"] = {"value": None, "exp": 0}

    ariaops = session_state.get("ariaops")
    if not isinstance(ariaops, dict):
        ariaops = {}
        session_state["ariaops"] = ariaops

    ariaops.setdefault("ready", False)
    ariaops.setdefault("tools", [])
    ariaops.setdefault("handlers", {})
    ariaops.setdefault("client", None)
    ariaops.setdefault("settings", None)
    return session_state


def _session_token_cache(session_state: dict[str, Any] | None) -> dict[str, Any]:
    return _ensure_session_state(session_state)["llm_token"]


def _session_ariaops_state(session_state: dict[str, Any] | None) -> dict[str, Any]:
    return _ensure_session_state(session_state)["ariaops"]


def _jwt_claims(token: str) -> dict[str, Any] | None:
    """Decode JWT claims without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        part = parts[1]
        part += "=" * (-len(part) % 4)
        payload = base64.urlsafe_b64decode(part)
        claims = json.loads(payload)
        if not isinstance(claims, dict):
            return None
        return claims
    except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
        return None
    except Exception:
        return None


def _jwt_exp(token: str) -> int:
    """Decode JWT exp claim without verifying signature."""
    claims = _jwt_claims(token)
    if not claims:
        return 0
    try:
        return int(claims.get("exp", 0))
    except Exception:
        return 0


def _token_valid(token: str | None, slack: int = 60) -> bool:
    return bool(token) and _jwt_exp(token) > time.time() + slack


def get_llm_token(session_state: dict[str, Any] | None = None) -> str:
    """Return a valid JWT from cache or env var, raise if none available."""
    if session_state is None:
        env = os.environ.get("LITELLM_TOKEN", "").strip()
        if _token_valid(env):
            _token_cache.update({"value": env, "exp": _jwt_exp(env)})
            return env
    cache = _session_token_cache(session_state)
    token = cache.get("value")
    if isinstance(token, str) and _token_valid(token):
        return token
    raise RuntimeError("No valid token. Paste a JWT in the LLM Gateway panel.")


def token_status_text(session_state: dict[str, Any] | None = None) -> str:
    if session_state is None:
        tok = _token_cache.get("value") or os.environ.get("LITELLM_TOKEN", "").strip()
    else:
        tok = _session_token_cache(session_state).get("value")
    if not tok:
        return "No token set"
    if not _token_valid(tok):
        return "Token expired or invalid"
    exp = _jwt_exp(tok)
    dt = datetime.datetime.fromtimestamp(exp).strftime("%Y-%m-%d %H:%M:%S")
    mins = max(0, int((exp - time.time()) / 60))
    return f"Valid — expires {dt} ({mins} min remaining)"


def apply_manual_token(token_text: str, session_state: dict[str, Any] | None = None) -> str:
    state = _ensure_session_state(session_state)
    tok = token_text.strip()
    if not tok:
        return "No token provided."
    claims = _jwt_claims(tok)
    if not claims:
        return "Token is not a valid JWT."
    exp = _jwt_exp(tok)
    if exp <= 0:
        return "Token is missing a valid expiration claim."
    if exp and exp < time.time():
        return "Token is already expired."
    _session_token_cache(state).update({"value": tok, "exp": exp})
    if session_state is None:
        os.environ["LITELLM_TOKEN"] = tok
        return token_status_text()
    return token_status_text(state)


# ─── Azure SSO — pure Python OAuth2 implicit flow ─────────────────────────────
# Ported from arcus/get-jwt.sh. No bash dependency, no pipefail issues.
# Proxy note: the browser handles its own proxy for Azure AD. Python does not
# make direct HTTP calls to Azure AD — all auth is browser-redirect based.

_AZURE_TENANT_ID = "05764a73-8c6f-4538-83cd-413f1e1b5665"
_AZURE_CLIENT_ID = "8bce86e2-607e-402d-87eb-4cff2463e6f7"
_AZURE_SCOPES = (
    "openid profile email offline_access "
    "https://graph.microsoft.com/GroupMember.Read.All "
    "https://graph.microsoft.com/User.Read"
)
_AZURE_CALLBACK_HOST = "127.0.0.1"
_AZURE_CALLBACK_PORTS = [9999, 8888, 7777]
_AZURE_CALLBACK_PATH = "/callback"


def _find_free_port() -> int | None:
    for port in _AZURE_CALLBACK_PORTS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((_AZURE_CALLBACK_HOST, port))
            s.close()
            return port
        except OSError:
            continue
    return None


def _build_auth_url(port: int, state: str | None = None, nonce: str | None = None) -> str:
    redirect_uri = f"http://localhost:{port}{_AZURE_CALLBACK_PATH}"
    params = {
        "client_id": _AZURE_CLIENT_ID,
        "response_type": "id_token",
        "redirect_uri": redirect_uri,
        "response_mode": "fragment",
        "scope": _AZURE_SCOPES,
        "state": state or secrets.token_urlsafe(24),
        "nonce": nonce or secrets.token_urlsafe(24),
        "audience": _AZURE_CLIENT_ID,
    }
    return f"https://login.microsoftonline.com/{_AZURE_TENANT_ID}/oauth2/v2.0/authorize?" + urllib.parse.urlencode(
        params
    )


@dataclass
class _OAuthFlow:
    state: str
    nonce: str
    event: threading.Event = field(default_factory=threading.Event)
    result: str | None = None


def _validate_oauth_callback(id_token: str, returned_state: str, flow: _OAuthFlow) -> tuple[bool, str]:
    if returned_state != flow.state:
        return False, "ERROR:state_mismatch"
    claims = _jwt_claims(id_token)
    if not claims:
        return False, "ERROR:invalid_token"
    if claims.get("nonce") != flow.nonce:
        return False, "ERROR:nonce_mismatch"
    return True, id_token


def _make_azure_callback_handler(flow: _OAuthFlow):
    class _AzureCallbackHandler(BaseHTTPRequestHandler):
        """Minimal HTTP handler for the Azure AD OAuth2 implicit-flow callback."""

        def log_message(self, format, *args):  # silence default access log
            pass

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != _AZURE_CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return

            query = urllib.parse.parse_qs(parsed.query)

            if "id_token" in query:
                returned_state = query.get("state", [""])[0]
                ok, result = _validate_oauth_callback(query["id_token"][0], returned_state, flow)
                flow.result = result
                flow.event.set()
                if ok:
                    self._html(
                        200, "<h1>Authenticated!</h1><p>You may close this tab.</p><script>window.close();</script>"
                    )
                else:
                    self._html(400, "<h1>Authentication failed.</h1><p>State or nonce validation failed.</p>")
                threading.Thread(target=self.server.shutdown, daemon=True).start()

            elif "error" in query:
                returned_state = query.get("state", [""])[0]
                if returned_state != flow.state:
                    flow.result = "ERROR:state_mismatch"
                else:
                    err = query.get("error", ["unknown"])[0]
                    flow.result = f"ERROR:{err}"
                flow.event.set()
                self._html(400, "<h1>Auth failed.</h1>")
                threading.Thread(target=self.server.shutdown, daemon=True).start()

            else:
                self._html(
                    200,
                    """
                    <h2>Completing authentication...</h2>
                    <script>
                    (function(){
                      var h = window.location.hash.slice(1);
                      if (!h) { document.body.innerHTML='<p>No token in URL.</p>'; return; }
                      var p = new URLSearchParams(h);
                      var t = p.get('id_token'), e = p.get('error'), s = p.get('state');
                      if (t) {
                        window.location.href='/callback?id_token='+encodeURIComponent(t)
                          +'&state='+encodeURIComponent(s || '');
                      } else if (e) {
                        window.location.href='/callback?error='+encodeURIComponent(e)
                          +'&state='+encodeURIComponent(s || '')
                          +'&error_description='+encodeURIComponent(p.get('error_description')||'');
                      } else {
                        document.body.innerHTML='<p>Unexpected response.</p>';
                      }
                    })();
                    </script>""",
                )

        def _html(self, code: int, body: str):
            content = (
                f"<html><body style='font-family:sans-serif;text-align:center;padding-top:8%'>{body}</body></html>"
            )
            data = content.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return _AzureCallbackHandler


def _start_callback_server(flow: _OAuthFlow) -> tuple[HTTPServer, int]:
    handler = _make_azure_callback_handler(flow)
    last_error: OSError | None = None
    for port in _AZURE_CALLBACK_PORTS:
        try:
            server = HTTPServer((_AZURE_CALLBACK_HOST, port), handler)
            return server, port
        except OSError as exc:
            last_error = exc
            continue
    raise OSError(f"No free callback port (tried {_AZURE_CALLBACK_PORTS}): {last_error}")


async def fetch_token_stream(session_state: dict[str, Any] | None = None) -> AsyncGenerator[str, None]:
    """Pure-Python Azure AD OAuth2 implicit flow. No bash, no proxy needed."""
    state = _ensure_session_state(session_state)
    flow = _OAuthFlow(state=secrets.token_urlsafe(24), nonce=secrets.token_urlsafe(24))

    try:
        server, port = await asyncio.to_thread(_start_callback_server, flow)
    except OSError:
        yield f"No free callback port (tried {_AZURE_CALLBACK_PORTS}). Free one of those ports and retry."
        return

    threading.Thread(target=server.serve_forever, daemon=True).start()
    auth_url = _build_auth_url(port, state=flow.state, nonce=flow.nonce)

    try:
        webbrowser.open(auth_url)
        yield (
            "Browser opened. If nothing appeared, open this URL manually:\n\n"
            f"{auth_url}\n\nWaiting for authentication (up to 5 min)..."
        )
    except Exception:
        yield (
            "Open this URL in your browser to authenticate:\n\n"
            f"{auth_url}\n\nWaiting for authentication (up to 5 min)..."
        )

    try:
        for _ in range(300):
            await asyncio.sleep(1)
            if flow.event.is_set():
                break
        else:
            yield "Timeout - no authentication received within 5 minutes."
            return

        result = flow.result
        if not result:
            yield "No token received."
        elif result.startswith("ERROR:"):
            yield f"Authentication failed: {result[6:]}"
        else:
            yield apply_manual_token(result, state)
    finally:
        with contextlib.suppress(Exception):
            server.shutdown()
        with contextlib.suppress(Exception):
            server.server_close()


# ─── model discovery ───────────────────────────────────────────────────────────
def fetch_models(base_url: str, session_state: dict[str, Any] | None = None) -> tuple[list[str], str | None]:
    """Return (model_list, error_message). error_message is None on success."""
    if not base_url or not base_url.strip().startswith("http"):
        return [], "Gateway URL not set — enter a URL and try again."
    try:
        tok = get_llm_token(session_state)
        r = SESSION.get(
            f"{base_url.rstrip('/')}/model/info",
            headers={"Authorization": f"Bearer {tok}"},
            verify=False,
            timeout=20,
        )
        if r.status_code != 200:
            return [], f"HTTP {r.status_code} from /model/info"
        seen, out = set(), []
        for m in r.json().get("data", []):
            alias = m.get("model_name", "")
            mode = (m.get("model_info") or {}).get("mode")
            litellm_model = (m.get("litellm_params") or {}).get("model", "")
            provider = litellm_model.split("/")[0] if "/" in litellm_model else ""
            if alias and mode in CHAT_MODES and provider in HOSTED_PROVIDERS and alias not in seen:
                seen.add(alias)
                out.append(alias)
        if not out:
            return [], "No chat-capable models returned by gateway"
        return sorted(out), None
    except Exception as exc:
        return [], str(exc)


@contextlib.contextmanager
def _ariaops_session_context(session_state: dict[str, Any] | None = None):
    if session_state is None:
        yield
        return

    ariaops_state = _session_ariaops_state(session_state)
    settings = ariaops_state.get("settings")
    client = ariaops_state.get("client")
    if settings is None or client is None:
        yield
        return

    import ariaops_mcp.client as client_mod
    from ariaops_mcp.config import reset_settings_override, set_settings_override

    settings_token = set_settings_override(settings)
    client_token = client_mod.set_client_override(client)
    try:
        yield
    finally:
        client_mod.reset_client_override(client_token)
        reset_settings_override(settings_token)


async def init_ariaops(
    host: str,
    username: str,
    password: str,
    auth_source: str = "local",
    verify_ssl: bool = False,
    session_state: dict[str, Any] | None = None,
) -> str:
    """Import ariaops_mcp, reset singletons, load tool registry."""
    global _ariaops_ready, _mcp_tools, _mcp_handlers

    if not host.strip():
        return "AriaOps host is required."

    try:
        import ariaops_mcp.client as client_mod
        from ariaops_mcp.config import Settings
        from ariaops_mcp.server import _TOOL_DEFS, _TOOL_HANDLERS

        state = _ensure_session_state(session_state)
        ariaops_state = _session_ariaops_state(state)

        old_client = ariaops_state.get("client")
        if old_client is not None:
            with contextlib.suppress(Exception):
                await old_client.close()

        settings = Settings(
            host=host.strip(),
            username=username.strip(),
            password=password.strip(),
            auth_source=(auth_source or "local").strip(),
            verify_ssl=verify_ssl,
        )
        client = client_mod.AriaOpsClient()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in _TOOL_DEFS
        ]
        handlers = dict(_TOOL_HANDLERS)

        ariaops_state.update(
            {
                "ready": True,
                "tools": tools,
                "handlers": handlers,
                "client": client,
                "settings": settings,
            }
        )

        if session_state is None:
            _mcp_tools = tools
            _mcp_handlers = handlers
            _ariaops_ready = True
            _GLOBAL_SESSION_STATE["ariaops"].update(ariaops_state)

        return f"Connected — {len(tools)} tools available"

    except Exception as exc:
        if session_state is None:
            _ariaops_ready = False
            _mcp_tools = []
            _mcp_handlers = {}
            _GLOBAL_SESSION_STATE["ariaops"].update(
                {
                    "ready": False,
                    "tools": [],
                    "handlers": {},
                    "client": None,
                    "settings": None,
                }
            )
        else:
            ariaops_state = _session_ariaops_state(session_state)
            ariaops_state.update(
                {
                    "ready": False,
                    "tools": [],
                    "handlers": {},
                    "client": None,
                    "settings": None,
                }
            )
        return f"Failed to connect: {exc}"


def ariaops_status_text(session_state: dict[str, Any] | None = None) -> str:
    if session_state is None:
        if _ariaops_ready:
            return f"Connected ({len(_mcp_tools)} tools)"
        return "Not connected"
    ariaops_state = _session_ariaops_state(session_state)
    if ariaops_state.get("ready"):
        return f"Connected ({len(ariaops_state.get('tools', []))} tools)"
    return "Not connected"


# ─── LLM gateway call ──────────────────────────────────────────────────────────
def _chat_completion(
    base_url: str,
    model: str,
    messages: list,
    tools: list,
    max_tokens: int,
    session_state: dict[str, Any] | None = None,
) -> dict:
    """Synchronous OpenAI-compatible chat completion call."""
    try:
        tok = get_llm_token(session_state)
        payload: dict = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        r = SESSION.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json=payload,
            verify=False,
            timeout=180,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        return {"error": f"HTTP {exc.response.status_code}: {exc.response.text[:300]}"}
    except Exception as exc:
        return {"error": str(exc)}


# ─── MCP tool execution ────────────────────────────────────────────────────────
async def _call_mcp_tool(name: str, arguments: dict) -> str:
    return await _call_mcp_tool_for_state(name, arguments, None)


async def _call_mcp_tool_for_state(name: str, arguments: dict, session_state: dict[str, Any] | None = None) -> str:
    handlers = _mcp_handlers if session_state is None else _session_ariaops_state(session_state).get("handlers", {})
    handler = handlers.get(name)
    if not handler:
        return json.dumps({"error": f"Unknown tool: {name}"})
    try:
        with _ariaops_session_context(session_state):
            return await handler(arguments)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def _tool_call_md(tool_name: str, args: dict, result: str, show_result: bool) -> str:
    """Format a tool call as markdown for display in chat."""
    args_str = json.dumps(args, indent=2)
    lines = [f"\n> **Tool:** `{tool_name}`", f"> ```json\n> {args_str}\n> ```"]
    if show_result:
        try:
            result_str = json.dumps(json.loads(result), indent=2)
        except Exception:
            result_str = result
        if len(result_str) > 1500:
            result_str = result_str[:1500] + "\n... (truncated)"
        lines.append(f"> <details><summary>Result</summary>\n>\n> ```json\n> {result_str}\n> ```\n> </details>")
    return "\n".join(lines) + "\n\n"


# ─── agentic loop ──────────────────────────────────────────────────────────────
async def _agentic_loop(
    base_url: str,
    model: str,
    messages: list,
    max_tokens: int,
    show_tools: bool,
    session_state: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """
    Run LLM → tool calls → results → LLM loop until done or max rounds.
    Returns (final_assistant_text, tool_calls_markdown).
    """
    if session_state is None:
        tools = _mcp_tools if _ariaops_ready else []
    else:
        ariaops_state = _session_ariaops_state(session_state)
        tools = ariaops_state.get("tools", []) if ariaops_state.get("ready") else []
    tool_calls_md = ""

    for _ in range(MAX_TOOL_ROUNDS):
        body = await asyncio.to_thread(_chat_completion, base_url, model, messages, tools, max_tokens, session_state)

        if "error" in body:
            return f"LLM error: {body['error']}", tool_calls_md

        choices = body.get("choices", [])
        if not choices:
            return "No response from LLM.", tool_calls_md

        choice = choices[0]
        message = choice.get("message", {})
        messages.append(message)

        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return message.get("content") or "", tool_calls_md

        # execute each tool call
        tool_results = []
        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            result = await _call_mcp_tool_for_state(tool_name, args, session_state)
            tool_calls_md += _tool_call_md(tool_name, args, result, show_tools)

            tool_results.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "name": tool_name,
                    "content": result,
                }
            )

        messages.extend(tool_results)

    return "Reached maximum tool call rounds without a final answer.", tool_calls_md


# ─── Gradio chat handler ────────────────────────────────────────────────────────
async def chat_fn(
    user_message: str,
    chat_history: list,
    system_prompt: str,
    base_url: str,
    model: str,
    max_tokens: int,
    show_tools: bool,
    session_state: dict[str, Any] | None = None,
) -> tuple[list, list, str, dict[str, Any]]:
    """
    Gradio event handler. history is a list of [user, assistant] pairs.
    Returns (updated_chatbot, updated_state, cleared_input).
    """
    state = _ensure_session_state(session_state)
    if not user_message.strip():
        return chat_history, chat_history, "", state

    def _err(msg: str) -> tuple[list, list, str, dict[str, Any]]:
        h = chat_history + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": f"**Error:** {msg}"},
        ]
        return h, h, "", state

    if not base_url.strip():
        return _err("Please configure the LLM Gateway URL.")
    if not model:
        return _err("Please select a model.")

    # build message list for LLM from {"role","content"} history
    messages: list[dict] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    for msg in chat_history:
        if msg.get("role") in ("user", "assistant"):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    final_text, tool_calls_md = await _agentic_loop(base_url, model, messages, max_tokens, show_tools, state)

    response = (tool_calls_md + final_text) if (show_tools and tool_calls_md) else final_text
    response = response or "(no response)"

    updated = chat_history + [
        {"role": "user", "content": user_message},
        {"role": "assistant", "content": response},
    ]
    return updated, updated, "", state


# ─── Gradio UI ─────────────────────────────────────────────────────────────────
def build_ui() -> gr.Blocks:
    with gr.Blocks(title="AriaOps MCP Chatbot") as demo:
        gr.Markdown(
            "# AriaOps MCP Chatbot\nChat with your VMware Aria Operations environment using LLM-driven tool calls."
        )

        with gr.Row():
            # ── left column: settings ───────────────────────────────────────
            with gr.Column(scale=1, min_width=320):
                # AriaOps connection
                with gr.Accordion("AriaOps Connection", open=True):
                    ariaops_host = gr.Textbox(
                        label="Host",
                        placeholder="aria-ops.example.com",
                        value=os.environ.get("ARIAOPS_HOST", ""),
                    )
                    ariaops_user = gr.Textbox(
                        label="Username",
                        value=os.environ.get("ARIAOPS_USERNAME", ""),
                    )
                    ariaops_pass = gr.Textbox(
                        label="Password",
                        type="password",
                        value=os.environ.get("ARIAOPS_PASSWORD", ""),
                    )
                    with gr.Row():
                        ariaops_auth_src = gr.Textbox(
                            label="Auth Source",
                            value="local",
                            scale=2,
                        )
                        ariaops_verify_ssl = gr.Checkbox(
                            label="Verify SSL",
                            value=False,
                            scale=1,
                        )
                    connect_btn = gr.Button("Connect", variant="primary", size="sm")
                    ariaops_status_box = gr.Textbox(
                        label="Status",
                        value=ariaops_status_text(),
                        interactive=False,
                    )

                # LLM gateway
                with gr.Accordion("LLM Gateway", open=True):
                    gr.Textbox(
                        label="Proxy Status",
                        value=proxy_status_text(),
                        interactive=False,
                    )
                    gw_url = gr.Textbox(
                        label="Gateway URL",
                        placeholder="https://litellm.example.com",
                        value=DEFAULT_GATEWAY_URL,
                    )
                    with gr.Row():
                        tok_status = gr.Textbox(
                            label="Token Status",
                            value=token_status_text(),
                            interactive=False,
                            scale=4,
                        )
                        refresh_tok_btn = gr.Button("Refresh", size="sm", scale=1)
                    with gr.Accordion("Paste JWT Token", open=True):
                        manual_token = gr.Textbox(
                            label="JWT",
                            type="password",
                            placeholder="eyJ0eXAiOiJKV1QiLCJhbGci…",
                        )
                        apply_tok_btn = gr.Button("Apply Token", size="sm")

                    fetch_tok_btn = gr.Button(
                        "Get Token via Azure SSO",
                        size="sm",
                        variant="secondary",
                    )

                    with gr.Row():
                        model_dd = gr.Dropdown(
                            choices=[],
                            value=None,
                            label="Model — click Load to discover from gateway",
                            interactive=True,
                            allow_custom_value=True,
                            scale=4,
                        )
                        load_models_btn = gr.Button("Load", size="sm", scale=1)
                    discover_err = gr.Markdown("")

                # chat settings
                with gr.Accordion("Chat Settings", open=False):
                    system_prompt_box = gr.Textbox(
                        label="System Prompt",
                        value=SYSTEM_PROMPT_DEFAULT,
                        lines=6,
                    )
                    max_tokens_slider = gr.Slider(
                        minimum=256,
                        maximum=16384,
                        value=4096,
                        step=256,
                        label="Max Tokens",
                    )
                    show_tools_cb = gr.Checkbox(
                        label="Show tool calls in chat",
                        value=True,
                    )

            # ── right column: chatbot ───────────────────────────────────────
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="AriaOps Assistant",
                    height=620,
                )
                with gr.Row():
                    msg_box = gr.Textbox(
                        label="",
                        placeholder="Ask about your infrastructure…",
                        scale=5,
                        lines=1,
                        show_label=False,
                        container=False,
                    )
                    send_btn = gr.Button("Send", variant="primary", scale=1)
                clear_btn = gr.Button("Clear Chat", size="sm")

        # ── state ────────────────────────────────────────────────────────────
        chat_state = gr.State([])
        session_state = gr.State(_new_session_state())

        # ── callbacks ────────────────────────────────────────────────────────
        connect_btn.click(
            init_ariaops,
            inputs=[ariaops_host, ariaops_user, ariaops_pass, ariaops_auth_src, ariaops_verify_ssl, session_state],
            outputs=ariaops_status_box,
        )

        apply_tok_btn.click(
            apply_manual_token,
            inputs=[manual_token, session_state],
            outputs=tok_status,
        )

        fetch_tok_btn.click(
            fetch_token_stream,
            inputs=[session_state],
            outputs=tok_status,
        )

        def do_refresh_token(state: dict[str, Any]):
            status = token_status_text(state)
            checked = datetime.datetime.now().strftime("%H:%M:%S")
            return f"{status} [checked {checked}]"

        refresh_tok_btn.click(
            do_refresh_token,
            inputs=[session_state],
            outputs=tok_status,
        )

        def do_load_models(url: str, state: dict[str, Any]):
            models, err = fetch_models(url, state)
            err_md = f"> **Model discovery failed:** `{err}`" if err else ""
            val = next((m for m in PREFERRED_MODELS if m in models), models[0] if models else None)
            return gr.Dropdown(choices=models, value=val), token_status_text(state), err_md

        load_models_btn.click(
            do_load_models,
            inputs=[gw_url, session_state],
            outputs=[model_dd, tok_status, discover_err],
        )

        send_btn.click(
            chat_fn,
            inputs=[
                msg_box,
                chat_state,
                system_prompt_box,
                gw_url,
                model_dd,
                max_tokens_slider,
                show_tools_cb,
                session_state,
            ],
            outputs=[chatbot, chat_state, msg_box, session_state],
        )
        msg_box.submit(
            chat_fn,
            inputs=[
                msg_box,
                chat_state,
                system_prompt_box,
                gw_url,
                model_dd,
                max_tokens_slider,
                show_tools_cb,
                session_state,
            ],
            outputs=[chatbot, chat_state, msg_box, session_state],
        )
        clear_btn.click(lambda: ([], [], _new_session_state()), inputs=[], outputs=[chatbot, chat_state, session_state])

    return demo


# ─── entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="AriaOps MCP Chatbot")
    parser.add_argument("--port", type=int, default=7860)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--share", action="store_true")
    args = parser.parse_args()

    demo = build_ui()
    demo.queue()
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        theme=gr.themes.Soft(),
    )


if __name__ == "__main__":
    main()

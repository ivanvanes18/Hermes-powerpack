"""Two Telegram topics, two real cached AIAgents, one shared openai-codex pool.

This is the integration test the per-topic affinity feature actually rests on.
Nothing about the pool, the recovery ladder or the agent cache is mocked: the
gateway's real ``TurnRunner._resolve_turn_agent`` builds real ``AIAgent``s from
the real ``CredentialPool``, they talk to a local fake Codex Responses endpoint
over HTTP, and the real ``agent_runtime_helpers.recover_with_credential_pool``
handles the 401/402/429 the endpoint returns for the pinned row's token.

Only two things are stood in for, neither of which is under test: the Telegram
transport (there is no adapter here — the turn is driven directly) and the
provider itself (a loopback HTTP server, so no network and no credentials).
"""

from __future__ import annotations

import base64
import http.server
import json
import socketserver
import threading
import time

import pytest
import yaml

from gateway.config import ChannelOverride, GatewayConfig, Platform, PlatformConfig
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.turn_context import TurnContext

PROVIDER = "openai-codex"
MODEL = "gpt-5.4"
CHAT_ID = "-1001234567890"
TOPIC_PINNED, TOPIC_SIBLING = "11", "22"

# Which pool row each topic pins, and the fake token each row carries.
PINNED_ROW, SIBLING_ROW, SPARE_ROW = "cred-pinned", "cred-sibling", "cred-spare"


def _jwt(account_id: str) -> str:
    """A JWT-shaped placeholder with a far-future ``exp`` so the pool never
    tries a (network) proactive refresh. Not a credential."""
    payload = json.dumps({
        "exp": int(time.time()) + 365 * 24 * 3600,
        "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
    }).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"e30.{encoded}.placeholder-signature"


TOKENS = {row: _jwt(row) for row in (PINNED_ROW, SIBLING_ROW, SPARE_ROW)}


# ── fake Codex Responses endpoint ──────────────────────────────────────────


class _FakeCodex:
    """Fails every request bearing ``doomed_token`` with ``status``; serves a
    minimal Responses SSE stream to anyone else. Records the bearer of each
    request so the test can prove which row actually made the call.

    Also stands in for the OAuth token endpoint (``/oauth/token``), which the
    401 ladder really POSTs to before it rotates. It answers ``invalid_grant``
    — a refresh token the provider no longer accepts — so the native
    refresh-then-rotate sequence runs end to end without leaving the loopback.
    """

    def __init__(self) -> None:
        self.doomed_token: str | None = None
        self.status = 429
        self.seen_bearers: list[str] = []
        self.token_endpoint_calls = 0
        self._lock = threading.Lock()

    def fail(self, token: str, status: int) -> None:
        with self._lock:
            self.doomed_token, self.status = token, status

    def record(self, bearer: str) -> bool:
        with self._lock:
            self.seen_bearers.append(bearer)
            return bearer == self.doomed_token

    def record_token_call(self) -> None:
        with self._lock:
            self.token_endpoint_calls += 1


_ERROR_BODIES = {
    401: {"error": {"type": "invalid_request_error", "message": "Unauthorized: token rejected"}},
    402: {"error": {"type": "billing_error", "message": "Insufficient credit balance"}},
    429: {"error": {"type": "rate_limit_error", "message": "You have hit your usage limit."}},
}


def _sse(events: list[dict]) -> bytes:
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events
    ) + b"data: [DONE]\n\n"


def _ok_stream(text: str) -> bytes:
    return _sse([
        {"type": "response.created", "sequence_number": 0,
         "response": {"id": "resp_fake", "object": "response", "status": "in_progress",
                      "model": MODEL, "output": []}},
        {"type": "response.output_text.delta", "sequence_number": 1,
         "item_id": "msg_fake", "output_index": 0, "content_index": 0, "delta": text},
        {"type": "response.completed", "sequence_number": 2,
         "response": {"id": "resp_fake", "object": "response", "status": "completed",
                      "model": MODEL, "output": [],
                      "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}}},
    ])


def _make_handler(state: _FakeCodex):
    class _Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # noqa: A002 - http.server API
            pass

        def _json(self, status: int, payload: dict, **headers: str) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for name, value in headers.items():
                self.send_header(name.replace("_", "-"), value)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - http.server API
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)
            if self.path.endswith("/oauth/token"):
                state.record_token_call()
                self._json(400, {"error": "invalid_grant",
                                 "error_description": "refresh token is no longer valid"})
                return
            bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            if state.record(bearer):
                # Retry-After: 0 keeps the REAL rate-limit retry sequence (retry
                # once, then rotate) instant and deterministic — the provider
                # header production already honours, not a patched clock.
                self._json(state.status, _ERROR_BODIES[state.status], Retry_After="0")
                return
            body = _ok_stream("rotated and recovered")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


class _ThreadingServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def codex_endpoint():
    state = _FakeCodex()
    server = _ThreadingServer(("127.0.0.1", 0), _make_handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{server.server_address[1]}"
    state.base_url = f"{root}/backend-api/codex"
    state.token_url = f"{root}/oauth/token"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def hermes_home(tmp_path, monkeypatch, codex_endpoint):
    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    # The pool must not adopt the host's real Codex CLI tokens.
    monkeypatch.setattr("hermes_cli.auth._import_codex_cli_tokens", lambda: None)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"model": {"default": MODEL, "provider": PROVIDER}}), encoding="utf-8")
    (home / "auth.json").write_text(json.dumps({
        "version": 1,
        "providers": {},
        "credential_pool": {PROVIDER: [
            {"id": row, "label": row, "auth_type": "oauth", "priority": idx,
             "source": "manual:device_code", "access_token": TOKENS[row],
             "refresh_token": f"rt-{row}", "base_url": codex_endpoint.base_url}
            for idx, row in enumerate((PINNED_ROW, SIBLING_ROW, SPARE_ROW))
        ]},
    }), encoding="utf-8")
    return home


# ── the gateway turn, for real ─────────────────────────────────────────────


def _gateway_config() -> GatewayConfig:
    return GatewayConfig(platforms={Platform.TELEGRAM: PlatformConfig(
        enabled=True, token="fake-bot-token",
        channel_overrides={
            TOPIC_PINNED: ChannelOverride(credential_id=PINNED_ROW),
            TOPIC_SIBLING: ChannelOverride(credential_id=SIBLING_ROW),
        },
    )})


@pytest.fixture
def runner(hermes_home, monkeypatch):
    monkeypatch.setattr("gateway.run._hermes_home", hermes_home)
    gw = object.__new__(GatewayRunner)
    gw.config = _gateway_config()
    gw.adapters = {}
    gw._session_model_overrides = {}
    gw._last_resolved_model = {}
    gw._service_tier = None
    gw._prefill_messages = None
    gw._fallback_model = None
    gw._session_db = None
    gw._agent_cache = {}
    gw._agent_cache_lock = threading.Lock()
    return gw


def _source(thread_id: str) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM, chat_id=CHAT_ID, chat_type="thread",
        user_id="u1", thread_id=thread_id,
    )


def _session_key(thread_id: str) -> str:
    return f"agent:main:telegram:thread:{CHAT_ID}:{thread_id}"


def _resolve_agent(runner: GatewayRunner, thread_id: str):
    """Drive the REAL gateway path: channel override -> runtime resolution ->
    turn route -> TurnRunner agent cache/build. Returns (agent, reused)."""
    from gateway.run_turn_runner import TurnRunner
    from run_agent import AIAgent

    source = _source(thread_id)
    user_config = {"model": {"default": MODEL, "provider": PROVIDER}}
    model, runtime_kwargs = runner._resolve_session_agent_runtime(
        source=source, session_key=_session_key(thread_id), user_config=user_config)
    turn_route = runner._resolve_turn_agent_config("hello", model, runtime_kwargs)
    ctx = TurnContext(
        source=source, session_key=_session_key(thread_id), session_id=None,
        user_config=user_config, enabled_toolsets=[], disabled_toolsets=[],
        AIAgent=AIAgent, message="hello",
    )
    return TurnRunner(runner, ctx)._resolve_turn_agent(
        turn_route, "telegram", "", 2, None, {},
    )


def test_two_topics_get_their_own_cached_agent_on_their_own_row(runner, codex_endpoint):
    pinned, pinned_reused = _resolve_agent(runner, TOPIC_PINNED)
    sibling, sibling_reused = _resolve_agent(runner, TOPIC_SIBLING)

    assert (pinned_reused, sibling_reused) == (False, False)
    assert pinned is not sibling
    assert pinned.api_key == TOKENS[PINNED_ROW]
    assert sibling.api_key == TOKENS[SIBLING_ROW]
    assert pinned._credential_pool is not sibling._credential_pool
    assert pinned._credential_pool.current().id == PINNED_ROW
    assert sibling._credential_pool.current().id == SIBLING_ROW

    # Same turn parameters again => the cache serves the same object per topic.
    again, reused = _resolve_agent(runner, TOPIC_PINNED)
    assert reused is True and again is pinned


def _stored_statuses(home) -> dict:
    rows = json.loads((home / "auth.json").read_text())["credential_pool"][PROVIDER]
    return {row["id"]: row.get("last_status") for row in rows}


@pytest.mark.parametrize("status", [401, 402, 429])
def test_provider_failure_rotates_the_failing_topic_and_leaves_its_sibling_alone(
    runner, codex_endpoint, hermes_home, monkeypatch, status,
):
    """The whole ladder, for real: a topic's pinned row starts failing, the
    agent's own pool rotates it onto another row, the turn completes on that
    row, the sibling topic's live agent/pool/cursor never move, and the failed
    row's status is persisted to the shared auth store."""
    # The 401 ladder refreshes before it rotates; keep that POST on the loopback.
    monkeypatch.setattr("hermes_cli.auth_codex.CODEX_OAUTH_TOKEN_URL", codex_endpoint.token_url)

    pinned, _ = _resolve_agent(runner, TOPIC_PINNED)
    sibling, _ = _resolve_agent(runner, TOPIC_SIBLING)
    sibling_pool = sibling._credential_pool
    assert pinned.api_key == TOKENS[PINNED_ROW]
    codex_endpoint.fail(TOKENS[PINNED_ROW], status)

    result = pinned.run_conversation("hello", conversation_history=[], task_id="affinity")

    # The pinned row really made the call, and a DIFFERENT row really served it.
    assert TOKENS[PINNED_ROW] in codex_endpoint.seen_bearers
    assert pinned.api_key in {TOKENS[SIBLING_ROW], TOKENS[SPARE_ROW]}
    assert pinned.api_key in codex_endpoint.seen_bearers
    assert pinned._credential_pool_entry_id in {SIBLING_ROW, SPARE_ROW}
    assert pinned._credential_pool.current().id == pinned._credential_pool_entry_id
    assert result.get("final_response") == "rotated and recovered"
    if status == 401:
        assert codex_endpoint.token_endpoint_calls >= 1, "401 must try a refresh before rotating"

    # LOCAL: the sibling topic's cached agent, pool object and cursor are untouched.
    assert sibling.api_key == TOKENS[SIBLING_ROW]
    assert sibling._credential_pool is not pinned._credential_pool
    assert sibling_pool.current().id == SIBLING_ROW
    assert {row.id: row.last_status for row in sibling_pool.entries()}[PINNED_ROW] is None

    # ACCOUNT-WIDE: the failed row's status is in the shared auth store on disk.
    assert _stored_statuses(hermes_home)[PINNED_ROW] in {"exhausted", "dead"}
    assert _stored_statuses(hermes_home)[SIBLING_ROW] is None


@pytest.mark.parametrize("status", [401, 402, 429])
def test_cache_rebuilds_the_rotated_topic_and_reuses_the_untouched_one(
    runner, codex_endpoint, monkeypatch, status,
):
    """Rotation changes the agent's api_key, which is part of the cache
    signature — so the rotated topic's next turn must build a fresh agent while
    the sibling topic stays a cache hit on the very same object."""
    monkeypatch.setattr("hermes_cli.auth_codex.CODEX_OAUTH_TOKEN_URL", codex_endpoint.token_url)
    pinned, _ = _resolve_agent(runner, TOPIC_PINNED)
    sibling, _ = _resolve_agent(runner, TOPIC_SIBLING)
    codex_endpoint.fail(TOKENS[PINNED_ROW], status)
    pinned.run_conversation("hello", conversation_history=[], task_id="affinity")

    rebuilt, rebuilt_reused = _resolve_agent(runner, TOPIC_PINNED)
    same_sibling, sibling_reused = _resolve_agent(runner, TOPIC_SIBLING)

    assert rebuilt_reused is False and rebuilt is not pinned
    assert rebuilt.api_key != TOKENS[PINNED_ROW], "the benched row must not come back next turn"
    assert sibling_reused is True and same_sibling is sibling
    assert same_sibling.api_key == TOKENS[SIBLING_ROW]

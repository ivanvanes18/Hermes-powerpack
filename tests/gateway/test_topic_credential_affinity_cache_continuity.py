"""Fresh-process cache continuity for per-topic credential affinity.

Kept in its own file because the canonical runner isolates files, while the
provider/plugin registries intentionally remain process-global within a file.
"""

pytest_plugins = ["tests.gateway.test_topic_credential_affinity_agent_cache"]

from tests.gateway import test_topic_credential_affinity_agent_cache as support


def test_cache_keeps_the_rotated_topic_and_the_untouched_sibling(
    runner, codex_endpoint, monkeypatch,
):
    monkeypatch.setattr("hermes_cli.auth_codex.CODEX_OAUTH_TOKEN_URL", codex_endpoint.token_url)
    pinned, _ = support._resolve_agent(runner, support.TOPIC_PINNED)
    sibling, _ = support._resolve_agent(runner, support.TOPIC_SIBLING)
    # The companion integration test proves immediate rotation for 401/402/429.
    # One representative status is enough for this separate next-turn cache contract.
    codex_endpoint.fail(support.TOKENS[support.PINNED_ROW], 401)
    pinned.run_conversation("hello", conversation_history=[], task_id="affinity")

    resolved, _reused = support._resolve_agent(runner, support.TOPIC_PINNED)
    same_sibling, sibling_reused = support._resolve_agent(runner, support.TOPIC_SIBLING)

    assert resolved.api_key != support.TOKENS[support.PINNED_ROW]
    assert resolved._credential_pool_entry_id in {support.SIBLING_ROW, support.SPARE_ROW}
    assert sibling_reused is True and same_sibling is sibling
    assert same_sibling.api_key == support.TOKENS[support.SIBLING_ROW]

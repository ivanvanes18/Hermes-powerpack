import asyncio
import contextlib
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


import gateway.run_inbound as gateway_run

_CARDS_PATH = pathlib.Path(__file__).parents[1] / "bin" / "send_buttons.py"
_CARDS_SPEC = importlib.util.spec_from_file_location("gptprof_send_buttons", _CARDS_PATH)
assert _CARDS_SPEC is not None and _CARDS_SPEC.loader is not None
cards = importlib.util.module_from_spec(_CARDS_SPEC)
sys.modules[_CARDS_SPEC.name] = cards
_CARDS_SPEC.loader.exec_module(cards)


def _load_sibling_module(name: str, filename: str):
    path = pathlib.Path(__file__).parents[1] / "bin" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


autoswitch = _load_sibling_module("gptprof_autoswitch_candidate", "gptprof_autoswitch.py")
refresh = _load_sibling_module("gptprof_refresh_candidate", "refresh_profiles.py")


def _adapter_pool_resolver():
    """Return the candidate adapter's real pool resolver."""
    from plugins.platforms.telegram.adapter import _gptprof_pool_entry_for_slug

    return _gptprof_pool_entry_for_slug


def _adapter_helper_runner():
    """Return the candidate adapter's real bound helper coroutine."""
    from plugins.platforms.telegram.adapter import TelegramAdapter

    adapter = object.__new__(TelegramAdapter)
    return adapter._run_gptprof_helper


class GptprofTopicFixTests(unittest.TestCase):
    def test_json_temp_is_private_at_creation(self):
        with tempfile.TemporaryDirectory() as td:
            target = pathlib.Path(td) / "profile.json"
            calls = []
            real_open = os.open

            def recording_open(path, flags, mode=0o777):
                calls.append((pathlib.Path(path), flags, mode))
                return real_open(path, flags, mode)

            old_umask = os.umask(0o022)
            try:
                with patch.object(cards.os, "open", side_effect=recording_open):
                    cards.save_json(str(target), {"access_token": "synthetic"})
            finally:
                os.umask(old_umask)

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][2], 0o600)
            self.assertTrue(calls[0][1] & os.O_EXCL)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_quick_context_precedence_and_stale_fields(self):
        source = types.SimpleNamespace(
            chat_id="-10042",
            thread_id="17",
            platform=types.SimpleNamespace(value="telegram"),
        )
        env = {
            "OPENAI_API_KEY": "must already be sanitized",
            "HERMES_QUICK_CHAT_ID": "old-chat",
            "HERMES_QUICK_THREAD_ID": "old-thread",
        }
        result = gateway_run._inject_quick_command_context(env, source)
        self.assertEqual(result["HERMES_QUICK_CHAT_ID"], "-10042")
        self.assertEqual(result["HERMES_QUICK_THREAD_ID"], "17")
        self.assertEqual(result["HERMES_QUICK_PLATFORM"], "telegram")
        self.assertEqual(result["OPENAI_API_KEY"], "must already be sanitized")

        no_thread = types.SimpleNamespace(chat_id="42", thread_id=None, platform="telegram")
        gateway_run._inject_quick_command_context(result, no_thread)
        self.assertNotIn("HERMES_QUICK_THREAD_ID", result)

    def test_per_invocation_target_and_optional_positive_thread(self):
        with patch.dict(
            os.environ,
            {
                "HERMES_QUICK_CHAT_ID": "-10042",
                "HERMES_QUICK_THREAD_ID": "17",
            },
            clear=False,
        ):
            self.assertEqual(cards._target_chat_id(), "-10042")
            self.assertEqual(cards._target_thread_id(), 17)
        for value in ("0", "1", "-1", "not-an-int", ""):
            with patch.dict(os.environ, {"HERMES_QUICK_THREAD_ID": value}, clear=False):
                self.assertIsNone(cards._target_thread_id())

    def test_main_omits_general_topic_thread_id_but_preserves_real_topic(self):
        class FakeBot:
            instances = []

            def __init__(self, token):
                self.messages = []
                self.instances.append(self)

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)

        class FakeButton:
            def __init__(self, text, callback_data):
                self.text = text
                self.callback_data = callback_data

        class FakeMarkup:
            def __init__(self, inline_keyboard):
                self.inline_keyboard = inline_keyboard

        async def exercise(thread_id):
            telegram = types.SimpleNamespace(
                Bot=FakeBot,
                InlineKeyboardButton=FakeButton,
                InlineKeyboardMarkup=FakeMarkup,
            )
            with patch.dict(
                os.environ,
                {
                    "HERMES_QUICK_CHAT_ID": "1",
                    "HERMES_QUICK_THREAD_ID": str(thread_id),
                },
                clear=False,
            ), patch.dict(sys.modules, {"telegram": telegram}), patch.object(
                cards, "load_cache", return_value={}
            ), patch.object(cards, "save_cache"), patch.object(
                cards, "load_profiles", return_value={}
            ), patch.object(
                cards, "overlay_runtime_profiles", side_effect=[{}, {}]
            ), patch.object(cards, "sync_from_intel64_openclaw"), patch.object(
                cards, "get_current_model", return_value="gpt-5.5"
            ), patch.object(cards, "get_current_profile", return_value=None):
                await cards.main()

        asyncio.run(exercise(1))
        asyncio.run(exercise(17))
        self.assertNotIn("message_thread_id", FakeBot.instances[-2].messages[0])
        self.assertEqual(FakeBot.instances[-1].messages[0]["message_thread_id"], 17)

    def test_main_omits_ambiguous_legacy_bootstrap_callback(self):
        auth = {
            "credential_pool": {
                "openai-codex": [
                    {
                        "source": "manual:device_code",
                        "id": "gptprof:profile1",
                        "profile": "profile1",
                        "access_token": "pool-a",
                        "refresh_token": "pool-r",
                    },
                    {
                        "source": "gptprof:profile1",
                        "id": "external-profile1",
                        "access_token": "alias-a",
                        "refresh_token": "alias-r",
                    },
                ]
            }
        }
        bootstrap = {
            "profile1": {
                "plan": "Pro",
                "access_token": "bootstrap-a",
                "refresh_token": "bootstrap-r",
            }
        }

        class FakeBot:
            instances = []

            def __init__(self, token):
                self.messages = []
                self.instances.append(self)

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)

        class FakeButton:
            def __init__(self, text, callback_data):
                self.text = text
                self.callback_data = callback_data

        class FakeMarkup:
            def __init__(self, inline_keyboard):
                self.inline_keyboard = inline_keyboard

        async def fake_fetch_usage(_session, _token, slug, _cache):
            return slug, {}

        async def exercise(auth_store):
            telegram = types.SimpleNamespace(
                Bot=FakeBot,
                InlineKeyboardButton=FakeButton,
                InlineKeyboardMarkup=FakeMarkup,
            )
            from hermes_cli import auth as auth_mod

            with patch.dict(
                os.environ, {"HERMES_QUICK_CHAT_ID": "1"}, clear=False
            ), patch.dict(sys.modules, {"telegram": telegram}), patch.object(
                cards, "AUTH_PATH", "/tmp/gptprof-topic-fix-auth.json"
            ), patch.object(cards, "load_json", return_value=auth_store), patch.object(
                cards, "load_profiles", return_value=bootstrap
            ), patch.object(cards, "load_cache", return_value={}), patch.object(
                cards, "save_cache"
            ), patch.object(cards, "sync_from_intel64_openclaw"), patch.object(
                cards, "get_current_model", return_value="gpt-5.5"
            ), patch.object(cards, "fetch_usage", side_effect=fake_fetch_usage), patch(
                "agent.credential_pool.load_pool",
                return_value=types.SimpleNamespace(
                    _lock=contextlib.nullcontext(),
                    _available_entries=lambda **_kwargs: ([], [])
                ),
            ), patch.object(
                auth_mod, "_file_lock", return_value=contextlib.nullcontext()
            ), patch.object(auth_mod, "_load_auth_store", return_value=auth_store):
                await cards.main()

        asyncio.run(exercise(auth))
        callbacks = [
            button.callback_data
            for row in FakeBot.instances[-1].messages[0]["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertFalse(any(callback.startswith("gptprof:profile1:") for callback in callbacks))

        asyncio.run(exercise({"gptprof": {"active_profile": "profile1"}}))
        bootstrap_callbacks = [
            button.callback_data
            for row in FakeBot.instances[-1].messages[0]["reply_markup"].inline_keyboard
            for button in row
        ]
        self.assertIn("gptprof:profile1:gpt-5.5", bootstrap_callbacks)

    def test_bootstrap_only_legacy_profile_still_catalogs(self):
        profiles = {"profile1": {"plan": "Pro"}}
        self.assertEqual(
            cards.profile_catalog(profiles),
            [("profile1", "Pro", "gpt-5.5")],
        )

    def test_generic_pool_catalog_has_no_bootstrap_placeholders(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "acct-a1",
                "label": "Work Account",
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "plan": "Pro",
            },
            {
                "source": "manual:device_code",
                "id": "acct-b2",
                "label": "Backup",
                "access_token": "access-secret-2",
                "refresh_token": "refresh-secret-2",
            },
        ]
        catalog = cards._pool_entry_catalog(pool)
        slugs = [slug for _item, slug in catalog]
        self.assertEqual(slugs, ["pool-acct-a1", "pool-acct-b2"])
        self.assertEqual(cards.profile_catalog({}), [])
        self.assertNotIn("access-secret", slugs)
        self.assertNotIn("refresh-secret", slugs)

    def test_generic_identity_is_exact_id_and_label_is_visible_only(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "acct-a1",
                "label": "Work Account",
                "access_token": "a",
                "refresh_token": "r",
            }
        ]
        item, slug = cards._pool_entry_catalog(pool)[0]
        self.assertIs(item, pool[0])
        self.assertEqual(slug, "pool-acct-a1")
        data = {"_pool_entry_generic": True, "_pool_entry_label": item["label"]}
        self.assertIn("Work Account", cards.profile_block(slug, "Pro", data, {}, False))
        self.assertNotIn("acct-a1 [", cards.profile_block(slug, "Pro", data, {}, False))

    def test_duplicate_labels_cannot_collide(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "acct-a1",
                "label": "Same label",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "manual:device_code",
                "id": "acct-b2",
                "label": "Same label",
                "access_token": "b",
                "refresh_token": "s",
            },
        ]
        self.assertEqual(
            [slug for _item, slug in cards._pool_entry_catalog(pool)],
            ["pool-acct-a1", "pool-acct-b2"],
        )

    def test_adapter_generic_resolution_is_exact_id(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "acct-a1",
                "label": "Work Account",
            },
            {
                "source": "manual:device_code",
                "id": "acct-b2",
                "label": "Backup",
            },
        ]
        resolve = _adapter_pool_resolver()
        self.assertIs(resolve(pool, "pool:acct-a1"), pool[0])
        self.assertIs(resolve(pool, "acct-a1"), None)
        self.assertIsNone(resolve(pool, "Work-Account"))

    def test_structured_generic_callback_cannot_cross_resolve_legacy_slug(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "x",
                "label": "Generic account",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "manual:device_code",
                "id": "gptprof:pool-x",
                "profile": "pool-x",
                "label": "Legacy account",
                "access_token": "b",
                "refresh_token": "s",
            },
        ]
        generic_callback = cards._callback_data("x", "gpt-5.5", generic=True)
        legacy_callback = cards._callback_data("pool-x", "gpt-5.5")
        self.assertEqual(
            generic_callback,
            "gptprof:pool:x:gpt-5.5",
        )
        self.assertEqual(legacy_callback, "gptprof:pool-x:gpt-5.5")
        self.assertNotEqual(generic_callback, legacy_callback)
        resolve = _adapter_pool_resolver()
        self.assertIs(resolve(pool, "pool:x"), pool[0])
        self.assertIs(resolve(pool, "pool-x"), pool[1])
        self.assertIsNone(resolve(pool, "x"))

    def test_duplicate_generic_ids_are_omitted_and_unresolvable(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "acct-dup",
                "label": "First",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "manual:device_code",
                "id": "acct-dup",
                "label": "Second",
                "access_token": "b",
                "refresh_token": "s",
            },
        ]
        self.assertEqual(cards._pool_entry_catalog(pool), [])
        self.assertIsNone(_adapter_pool_resolver()(pool, "pool:acct-dup"))

    def test_complete_and_incomplete_generic_duplicate_is_omitted_and_unresolvable(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "acct-mixed",
                "label": "Complete",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "manual:device_code",
                "id": "acct-mixed",
                "label": "Incomplete",
                "access_token": "a2",
            },
        ]
        self.assertEqual(cards._pool_entry_catalog(pool), [])
        self.assertIsNone(_adapter_pool_resolver()(pool, "pool:acct-mixed"))

    def test_legacy_duplicate_slug_is_unresolvable(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "gptprof:profile1",
                "profile": "profile1",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "manual:device_code",
                "id": "legacy-profile1-copy",
                "profile": "profile1",
                "access_token": "b",
                "refresh_token": "s",
            },
        ]
        self.assertIsNone(_adapter_pool_resolver()(pool, "profile1"))

    def test_legacy_id_alias_collision_is_omitted_by_producer(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "gptprof:profile1",
                "profile": "profile1",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "external",
                "id": "gptprof:profile1",
                "access_token": "b",
                "refresh_token": "s",
            },
        ]
        self.assertEqual(len(cards._pool_entry_matches(pool, "profile1")), 2)
        self.assertEqual(cards._pool_entry_catalog(pool), [])
        self.assertIsNone(_adapter_pool_resolver()(pool, "profile1"))

    def test_legacy_source_alias_collision_is_omitted_by_producer(self):
        pool = [
            {
                "source": "manual:device_code",
                "id": "legacy-profile1",
                "profile": "profile1",
                "access_token": "a",
                "refresh_token": "r",
            },
            {
                "source": "gptprof:profile1",
                "id": "external-profile1",
                "access_token": "b",
                "refresh_token": "s",
            },
        ]
        self.assertEqual(len(cards._pool_entry_matches(pool, "profile1")), 2)
        self.assertEqual(cards._pool_entry_catalog(pool), [])
        self.assertIsNone(_adapter_pool_resolver()(pool, "profile1"))

    def test_profile_block_redacts_persisted_refresh_error(self):
        token_like = "refresh failed: Bearer eyJhbGciOiJIUzI1NiJ9.secret-value"
        rendered = cards.profile_block(
            "profile1",
            "Pro",
            {"_refresh_error": token_like, "refresh_token": "r"},
            {},
            False,
        )
        self.assertIn("refresh failed · re-auth needed", rendered)
        self.assertNotIn(token_like, rendered)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", rendered)

    def test_profile_block_preserves_public_refresh_token_reused_code(self):
        rendered = cards.profile_block(
            "profile1",
            "Pro",
            {"_refresh_error": "refresh_token_reused", "refresh_token": "r"},
            {},
            False,
        )
        self.assertIn("refresh reused · new auth needed", rendered)

    def test_usage_error_display_is_safe_and_preserves_public_codes(self):
        token_like = "provider message leaks: Bearer eyJhbGciOiJIUzI1NiJ9.secret-value"
        reset = cards.reset_text(None, token_like)
        rendered = cards.profile_block(
            "profile1",
            "Pro",
            {},
            {"usage_error": token_like},
            False,
        )
        output = "\n".join((reset, rendered))
        self.assertNotIn(token_like, output)
        self.assertNotIn("provider message leaks", output)
        self.assertIn("usage unavailable", output)

        for code, message in (
            ("token_revoked", "token revoked · new auth needed"),
            ("token_expired", "token expired · new auth needed"),
        ):
            self.assertEqual(cards.reset_text(None, code), message)
            self.assertIn(
                f"⚠️ Usage API: {message}",
                cards.profile_block("profile1", "Pro", {}, {"usage_error": code}, False),
            )

    def test_canonical_pool_entry_overrides_stale_active_profile(self):
        auth = {
            "gptprof": {"active_profile": "stale"},
            "credential_pool": {
                "openai-codex": [
                    {
                        "source": "manual:device_code",
                        "id": "acct-live",
                        "label": "Live account",
                        "access_token": "a",
                        "refresh_token": "r",
                        "priority": 0,
                    },
                ],
            },
        }
        with patch.object(cards, "AUTH_PATH", "/tmp/gptprof-topic-fix-auth.json"), patch.object(
            cards, "load_json", return_value=auth
        ):
            self.assertEqual(cards.get_current_profile(), "pool-acct-live")

    def test_active_profile_skips_unusable_pool_entries(self):
        auth = {
            "credential_pool": {
                "openai-codex": [
                    {
                        "source": "manual:device_code",
                        "id": "acct-dead",
                        "access_token": "dead-a",
                        "refresh_token": "dead-r",
                        "priority": 0,
                        "last_status": "dead",
                    },
                    {
                        "source": "manual:device_code",
                        "id": "acct-incomplete",
                        "access_token": "incomplete-a",
                        "priority": 1,
                    },
                    {
                        "source": "manual:device_code",
                        "id": "acct-live",
                        "access_token": "live-a",
                        "refresh_token": "live-r",
                        "priority": 2,
                        "last_status": "ok",
                    },
                    {
                        "source": "manual:device_code",
                        "id": "acct-exhausted",
                        "access_token": "exhausted-a",
                        "refresh_token": "exhausted-r",
                        "priority": 3,
                        "last_status": "exhausted",
                    },
                ],
            }
        }
        with patch.object(cards, "AUTH_PATH", "/tmp/gptprof-topic-fix-auth.json"), patch.object(
            cards, "load_json", return_value=auth
        ):
            self.assertEqual(cards.get_current_profile(), "pool-acct-live")

    def test_no_usable_pool_entry_has_no_active_profile(self):
        auth = {
            "credential_pool": {
                "openai-codex": [
                    {
                        "source": "manual:device_code",
                        "id": "acct-dead",
                        "access_token": "dead-a",
                        "refresh_token": "dead-r",
                        "priority": 0,
                        "last_status": "dead",
                    },
                    {
                        "source": "manual:device_code",
                        "id": "acct-incomplete",
                        "access_token": "incomplete-a",
                        "priority": 1,
                    },
                ],
            }
        }
        with patch.object(cards, "AUTH_PATH", "/tmp/gptprof-topic-fix-auth.json"), patch.object(
            cards, "load_json", return_value=auth
        ):
            self.assertIsNone(cards.get_current_profile())

    def test_dead_only_pool_does_not_fall_back_to_stale_active_profile(self):
        auth = {
            "gptprof": {"active_profile": "stale"},
            "credential_pool": {
                "openai-codex": [
                    {
                        "source": "manual:device_code",
                        "id": "acct-dead",
                        "access_token": "dead-a",
                        "refresh_token": "dead-r",
                        "priority": 0,
                        "last_status": "dead",
                    },
                ],
            },
        }
        with patch.object(cards, "AUTH_PATH", "/tmp/gptprof-topic-fix-auth.json"), patch.object(
            cards, "load_json", return_value=auth
        ):
            self.assertIsNone(cards.get_current_profile())

    def test_main_does_not_mark_dead_only_pool_entry_active(self):
        auth = {
            "gptprof": {"active_profile": "stale"},
            "credential_pool": {
                "openai-codex": [
                    {
                        "source": "manual:device_code",
                        "id": "acct-dead",
                        "label": "Dead account",
                        "access_token": "dead-a",
                        "refresh_token": "dead-r",
                        "priority": 0,
                        "last_status": "dead",
                    },
                ],
            },
        }
        profiles = {
            "pool-acct-dead": {
                "_pool_entry_id": "acct-dead",
                "_pool_entry_generic": True,
                "_pool_entry_label": "Dead account",
                "access_token": "dead-a",
                "refresh_token": "dead-r",
                "plan": "Pro",
            }
        }

        class FakeBot:
            instances = []

            def __init__(self, token):
                self.messages = []
                self.instances.append(self)

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)

        class FakeButton:
            def __init__(self, text, callback_data):
                self.text = text
                self.callback_data = callback_data

        class FakeMarkup:
            def __init__(self, inline_keyboard):
                self.inline_keyboard = inline_keyboard

        async def fake_fetch_usage(_session, _token, slug, _cache):
            return slug, {}

        async def exercise():
            telegram = types.SimpleNamespace(
                Bot=FakeBot,
                InlineKeyboardButton=FakeButton,
                InlineKeyboardMarkup=FakeMarkup,
            )
            with patch.dict(
                os.environ, {"HERMES_QUICK_CHAT_ID": "1"}, clear=False
            ), patch.dict(sys.modules, {"telegram": telegram}), patch.object(
                cards, "AUTH_PATH", "/tmp/gptprof-topic-fix-auth.json"
            ), patch.object(cards, "load_json", return_value=auth), patch.object(
                cards, "load_cache", return_value={}
            ), patch.object(cards, "save_cache"), patch.object(
                cards, "load_profiles", return_value={}
            ), patch.object(
                cards, "overlay_runtime_profiles", side_effect=[profiles, profiles]
            ), patch.object(cards, "sync_from_intel64_openclaw"), patch.object(
                cards, "get_current_model", return_value="gpt-5.5"
            ), patch.object(cards, "fetch_usage", side_effect=fake_fetch_usage):
                await cards.main()

            rendered = FakeBot.instances[-1].messages[0]["text"]
            self.assertNotIn("🤖 GPT profile: Dead account", rendered)
            self.assertNotIn("· active", rendered)

        asyncio.run(exercise())

    def test_generic_autoswitch_reprioritizes_exact_row_without_duplicate(self):
        with tempfile.TemporaryDirectory() as td:
            auth_path = pathlib.Path(td) / "auth.json"
            auth_path.write_text(
                json.dumps({"credential_pool": {"openai-codex": [
                    {
                        "id": "acct-a",
                        "source": "manual:device_code",
                        "access_token": "a",
                        "refresh_token": "r",
                        "priority": 10,
                    },
                    {
                        "id": "acct-b",
                        "source": "manual:device_code",
                        "access_token": "b",
                        "refresh_token": "s",
                        "priority": 0,
                    },
                ]}}),
                encoding="utf-8",
            )
            with patch.object(autoswitch.gptprof, "AUTH_PATH", str(auth_path)), patch.object(
                autoswitch.gptprof,
                "sync_active_auth",
                side_effect=AssertionError("generic rows must not be re-imported"),
            ):
                autoswitch.switch_profile_auth(
                    "pool-acct-a",
                    {"_pool_entry_generic": True, "_pool_entry_id": "acct-a"},
                )

            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            pool = auth["credential_pool"]["openai-codex"]
            self.assertEqual([item["id"] for item in pool], ["acct-a", "acct-b"])
            self.assertEqual(next(item for item in pool if item["id"] == "acct-a")["priority"], 0)
            self.assertEqual(next(item for item in pool if item["id"] == "acct-b")["priority"], 10)

    def test_refresh_includes_generic_manual_pool_rows(self):
        generic = types.SimpleNamespace(
            id="acct-generic",
            source="manual:device_code",
            auth_type="oauth",
            access_token="access",
            refresh_token="refresh",
        )
        ignored = types.SimpleNamespace(
            id="other",
            source="external",
            auth_type="oauth",
            access_token="access",
            refresh_token="refresh",
        )

        class Pool:
            def entries(self):
                return [generic, ignored]

            def _entry_needs_refresh(self, _entry):
                return False

        with patch.object(refresh, "load_pool", return_value=Pool()):
            code, results = refresh.refresh_profiles(force=False)

        self.assertEqual(code, 0)
        self.assertEqual([(item["slug"], item["state"]) for item in results], [("acct-generic", "fresh")])

    def test_card_runs_cooldown_lifecycle_before_reading_pool(self):
        calls = []

        class Pool:
            _lock = contextlib.nullcontext()

            def _available_entries(self, *, clear_expired, refresh):
                calls.append((clear_expired, refresh))
                return [], []

        with patch("agent.credential_pool.load_pool", return_value=Pool()), patch.object(
            cards,
            "load_json",
            return_value={"credential_pool": {"openai-codex": []}},
        ):
            cards.overlay_runtime_profiles({})

        self.assertEqual(calls, [(True, False)])

    def test_gptprof_helper_never_exposes_process_output(self):
        helper = _adapter_helper_runner()

        class Query:
            def __init__(self):
                self.answers = []

            async def answer(self, *, text):
                self.answers.append(text)

        class Process:
            def __init__(self, returncode):
                self.returncode = returncode

            async def communicate(self):
                return (
                    b"Bearer eyJhbGciOiJIUzI1NiJ9.stdout-secret",
                    b"provider stderr refresh-secret",
                )

            def kill(self):
                pass

        async def exercise():
            success = Query()
            with patch.object(
                asyncio,
                "create_subprocess_exec",
                return_value=Process(0),
            ):
                await helper(success, ["helper"], "safe success", timeout=1)
            self.assertEqual(success.answers, ["safe success"])
            self.assertNotIn("stdout-secret", success.answers[0])
            self.assertNotIn("refresh-secret", success.answers[0])

            failed = Query()
            with patch.object(
                asyncio,
                "create_subprocess_exec",
                return_value=Process(7),
            ):
                await helper(failed, ["helper"], "safe success", timeout=1)
            self.assertEqual(failed.answers, ["gptprof helper failed"])
            self.assertNotIn("stdout-secret", failed.answers[0])

            raised = Query()
            with patch.object(
                asyncio,
                "create_subprocess_exec",
                side_effect=RuntimeError("provider exception Bearer exception-secret"),
            ):
                await helper(raised, ["helper"], "safe success", timeout=1)
            self.assertEqual(raised.answers, ["gptprof helper failed"])
            self.assertNotIn("exception-secret", raised.answers[0])

            class HangingProcess:
                returncode = None

                def __init__(self):
                    self.started = asyncio.Event()
                    self.killed = False
                    self.communicate_calls = 0

                async def communicate(self):
                    self.communicate_calls += 1
                    if self.killed:
                        return b"", b""
                    self.started.set()
                    await asyncio.Event().wait()

                def kill(self):
                    self.killed = True

            cancelled = Query()
            hanging = HangingProcess()
            with patch.object(asyncio, "create_subprocess_exec", return_value=hanging):
                task = asyncio.create_task(
                    helper(cancelled, ["helper"], "safe success", timeout=60)
                )
                await hanging.started.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertTrue(hanging.killed)
            self.assertGreaterEqual(hanging.communicate_calls, 2)
            self.assertEqual(cancelled.answers, [])

        asyncio.run(exercise())

    def test_header_uses_generic_pool_label_for_active_entry(self):
        data = {
            "_pool_entry_generic": True,
            "_pool_entry_label": "Human account",
        }
        self.assertIn(
            "Human account",
            cards.profile_block("pool-acct", "Pro", data, {}, True),
        )

    def test_generic_model_fallback_preserves_current_model(self):
        profiles = {
            "pool-acct-a1": {
                "_pool_entry_id": "acct-a1",
                "_pool_entry_generic": True,
                "_pool_entry_label": "Reina main",
                "plan": "Pro",
            }
        }
        self.assertEqual(
            cards.profile_catalog(profiles, generic_model="reina-main"),
            [("pool-acct-a1", "Pro", "reina-main")],
        )

    def test_unsafe_generic_callback_is_omitted(self):
        profiles = {
            "acct:unsafe": {
                "_pool_entry_id": "acct:unsafe",
                "_pool_entry_generic": True,
                "_pool_entry_label": "Unsafe",
            }
        }
        self.assertEqual(cards.profile_catalog(profiles, generic_model="reina-main"), [])
        long_id = "a" * 55
        self.assertEqual(
            cards.profile_catalog(
                {long_id: {"_pool_entry_generic": True}},
                generic_model="reina-main",
            ),
            [],
        )

    def test_usage_window_labels_follow_api_seconds(self):
        week_only = cards.parse_usage(
            {
                "rate_limit": {
                    "primary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 25,
                    },
                    "secondary_window": None,
                }
            }
        )
        self.assertEqual([w["label"] for w in week_only["windows"]], ["Week"])
        self.assertIn("📅 Week: 75% left", cards.profile_block("acct", "Pro", {}, week_only, False))
        self.assertNotIn("5h: 75%", cards.profile_block("acct", "Pro", {}, week_only, False))

        both = cards.parse_usage(
            {
                "rate_limit": {
                    "primary_window": {
                        "limit_window_seconds": 18000,
                        "used_percent": 10,
                    },
                    "secondary_window": {
                        "limit_window_seconds": 604800,
                        "used_percent": 40,
                    },
                }
            }
        )
        self.assertEqual([w["label"] for w in both["windows"]], ["5h", "Week"])
        rendered = cards.profile_block("acct", "Pro", {}, both, False)
        self.assertIn("📊 5h: 90% left", rendered)
        self.assertIn("📅 Week: 60% left", rendered)


if __name__ == "__main__":
    unittest.main()

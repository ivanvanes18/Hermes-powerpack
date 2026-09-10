"""``task="video"`` must resolve through the vision-capable route, not the text one.

``video_analyze`` sends a base64 ``video_url`` part. If the auxiliary router treats the task as
plain text it selects a text-only backend, which either rejects the payload or — worse — answers
about a video it never received. The route branches share one predicate with ``vision`` so a
multimodal-capable client is chosen, while ``auxiliary.video.*`` still wins over
``auxiliary.vision.*`` because it arrives as an explicit argument.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from agent import auxiliary_client


def _resolve(task, *, resolved_provider="auto", resolved_model=None):
    return auxiliary_client._resolve_call_client(
        task,
        provider=None, model=None, base_url=None, api_key=None,
        resolved_provider=resolved_provider, resolved_model=resolved_model,
        resolved_base_url=None, resolved_api_key=None, resolved_api_mode=None,
        main_runtime=None, async_mode=False,
    )


class _Client:
    """Stand-in for a resolved provider client."""


def test_video_task_is_vision_capable():
    assert auxiliary_client._is_vision_capable_task("video")
    assert auxiliary_client._is_vision_capable_task("vision")
    assert not auxiliary_client._is_vision_capable_task("compression")
    assert not auxiliary_client._is_vision_capable_task(None)


@pytest.mark.parametrize("task", ["vision", "video"])
def test_media_task_resolves_through_the_vision_provider_chain(task):
    client = _Client()
    with (
        patch.object(auxiliary_client, "resolve_vision_provider_client",
                     return_value=("openrouter", client, "vision/model")) as vision_chain,
        patch.object(auxiliary_client, "_get_cached_client") as text_chain,
    ):
        route = _resolve(task)

    assert route.client is client
    assert route.final_model == "vision/model"
    vision_chain.assert_called_once()
    text_chain.assert_not_called()


def test_video_route_passes_its_own_resolved_provider_and_model_into_the_chain():
    """``auxiliary.video.provider``/``.model`` reach the chain as explicit args, so they
    outrank ``auxiliary.vision.*`` inside ``_resolve_task_provider_model``."""
    client = _Client()
    with (
        patch.object(auxiliary_client, "resolve_vision_provider_client",
                     return_value=("openrouter", client, "google/gemini-video")) as vision_chain,
        patch.object(auxiliary_client, "_get_cached_client"),
    ):
        _resolve("video", resolved_provider="openrouter", resolved_model="google/gemini-video")

    kwargs = vision_chain.call_args.kwargs
    assert kwargs["provider"] == "openrouter"
    assert kwargs["model"] == "google/gemini-video"


def test_text_task_still_uses_the_cached_text_client():
    client = _Client()
    with (
        patch.object(auxiliary_client, "resolve_vision_provider_client") as vision_chain,
        patch.object(auxiliary_client, "_get_cached_client",
                     return_value=(client, "text/model")) as text_chain,
        patch.object(auxiliary_client, "_effective_provider_for_client", return_value="openrouter"),
    ):
        route = _resolve("compression", resolved_provider="openrouter")

    assert route.client is client
    text_chain.assert_called_once()
    vision_chain.assert_not_called()


def test_video_falls_back_to_auto_vision_backends_when_its_provider_is_unavailable():
    client = _Client()
    with (
        patch.object(
            auxiliary_client, "resolve_vision_provider_client",
            side_effect=[("openrouter", None, None), ("nous", client, "auto/vision")],
        ) as vision_chain,
        patch.object(auxiliary_client, "_get_cached_client") as text_chain,
    ):
        route = _resolve("video", resolved_provider="openrouter", resolved_model="m")

    assert route.client is client
    assert vision_chain.call_count == 2
    assert vision_chain.call_args_list[1].kwargs["provider"] == "auto"
    text_chain.assert_not_called()

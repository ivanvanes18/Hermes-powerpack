"""Generic, bounded interactive cards for plugin gateway commands."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


_CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,23}$")
_ACTION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")
_MAX_CARDS = 512
_CARD_TTL_SECONDS = 15 * 60


def validate_telegram_callback_data(value: str) -> str:
    """Return callback data after enforcing Telegram's exact UTF-8 byte cap."""
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Telegram callback_data exceeds 64 UTF-8 bytes")
    return value


@dataclass(frozen=True)
class PluginCardButton:
    action: str
    label: str


@dataclass(frozen=True)
class PluginCard:
    card_id: str
    title: str
    body: str
    buttons: tuple[PluginCardButton, ...]
    source_chat_id: str
    source_thread_id: Optional[str]
    session_key: str
    action: Optional[Callable[[str, "PluginCommandContext"], Any]] = None
    source_user_id: Optional[str] = None
    plugin_context: Any = None


@dataclass
class PluginCommandContext:
    event: Any
    source: Any
    session_key: str
    chat_id: str
    thread_id: Optional[str]
    user_id: str
    adapter: Any = None
    plugin_context: Any = None
    request_text: Optional[Callable[..., Any]] = None


class PluginCardRegistry:
    """Thread-safe, expiring, one-shot registry bound to the originating topic."""

    def __init__(self, *, ttl_seconds: float = _CARD_TTL_SECONDS, max_cards: int = _MAX_CARDS, clock=time.monotonic):
        self._cards: dict[str, tuple[PluginCard, Callable[[str, PluginCommandContext], Any], float]] = {}
        self._lock = threading.RLock()
        self._ttl = max(0.001, float(ttl_seconds))
        self._max_cards = max(1, int(max_cards))
        self._clock = clock

    @staticmethod
    def _validate_card(card: PluginCard) -> None:
        if not isinstance(card.card_id, str) or not _CARD_ID_RE.fullmatch(card.card_id):
            raise ValueError("card_id must be an opaque bounded identifier")
        if not isinstance(card.session_key, str) or not card.session_key.strip():
            raise ValueError("card session_key must be nonempty")
        actions = [button.action for button in card.buttons]
        if len(actions) != len(set(actions)) or any(not _ACTION_RE.fullmatch(action) for action in actions):
            raise ValueError("card actions must be unique bounded identifiers")

    def add(self, card: PluginCard, action: Optional[Callable[[str, PluginCommandContext], Any]] = None) -> PluginCard:
        self._validate_card(card)
        callback = action or card.action
        if not callable(callback):
            raise ValueError("card callback is required")
        with self._lock:
            self._purge_locked()
            if card.card_id not in self._cards and len(self._cards) >= self._max_cards:
                raise RuntimeError("plugin card registry is full")
            self._cards[card.card_id] = (card, callback, self._clock() + self._ttl)
        return card

    def get(self, card_id: str) -> Optional[PluginCard]:
        with self._lock:
            self._purge_locked()
            item = self._cards.get(card_id)
            return item[0] if item else None

    def dispatch(self, card_id: str, action: str, context: PluginCommandContext) -> Any:
        if not isinstance(action, str) or not _ACTION_RE.fullmatch(action):
            raise ValueError("unknown card action")
        with self._lock:
            self._purge_locked()
            item = self._cards.get(card_id)
            if item is None:
                raise KeyError("card expired")
            card, callback, _expires = item
            if str(context.chat_id) != str(card.source_chat_id) or str(context.thread_id) != str(card.source_thread_id):
                raise PermissionError("card is bound to another Telegram chat/topic")
            if card.source_user_id is not None and str(context.user_id) != str(card.source_user_id):
                raise PermissionError("card is bound to another user")
            if action not in {button.action for button in card.buttons}:
                raise ValueError("unknown card action")
            # Claim before invoking plugin code: callbacks cannot be repeated, even if they fail.
            del self._cards[card_id]
        context.session_key = card.session_key
        context.plugin_context = card.plugin_context or context.plugin_context
        return callback(action, context)

    def discard(self, card_id: str) -> None:
        with self._lock:
            self._cards.pop(card_id, None)

    def clear(self) -> None:
        """Invalidate every card owned by this manager during unload/reload."""
        with self._lock:
            self._cards.clear()

    def _purge_locked(self) -> None:
        now = self._clock()
        for card_id, (_card, _callback, expires) in list(self._cards.items()):
            if expires <= now:
                del self._cards[card_id]

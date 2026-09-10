"""Stable mandatory policy that binds execution to the user's named owner."""
from __future__ import annotations

SCOPE_OWNERSHIP_GUIDANCE = (
    "### Scope-owner lock\n"
    "Durability, autonomy, multi-agent, scale, or reliability requirements do not authorize replacing the assigned "
    "object. If the user names Hermes, its ordinary tools, Shaw, `/goal`, or the current runtime, keep work inside that "
    "owner. A loaded skill informs execution; it never transfers task ownership. Use another runtime only when it is a "
    "strict prerequisite or the user explicitly authorizes migration; otherwise report it separately instead of "
    "acting on it.\n"
)


def scope_ownership_guidance() -> str:
    """Return the policy-independent lock against semantic owner substitution."""
    return SCOPE_OWNERSHIP_GUIDANCE

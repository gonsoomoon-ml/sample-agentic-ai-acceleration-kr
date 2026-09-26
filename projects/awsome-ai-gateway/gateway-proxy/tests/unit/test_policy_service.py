"""models_fragment — the `managedSettings` slice that policy-helper puts in front of
Claude Code, computed from the same rules the gateway already enforces:

  allowed = user_allowed_models if any, else team_allowed_models if any, else ALL
  usable  = allowed ∩ (ACTIVE models this app may use)

Claude Code distinguishes an absent `availableModels` (no restriction) from `[]`
(nothing allowed), so "no restriction" must be expressed by omitting the key.
"""
from __future__ import annotations

import pytest

from app.services.policy_service import models_fragment

pytestmark = pytest.mark.unit

ACTIVE = ["claude-haiku-4-5-20251001", "claude-opus-5-5", "claude-sonnet-5"]


def test_no_restriction_anywhere_emits_nothing():
    fragment = models_fragment(
        user_aliases=[], team_aliases=[], app_models=ACTIVE, active_models=ACTIVE,
        default_model=None,
    )

    assert fragment == {}


def test_team_list_restricts_and_is_enforced():
    fragment = models_fragment(
        user_aliases=[], team_aliases=["claude-sonnet-5"], app_models=ACTIVE,
        active_models=ACTIVE, default_model=None,
    )

    assert fragment == {"availableModels": ["claude-sonnet-5"], "enforceAvailableModels": True}


def test_user_list_overrides_team_list():
    # Same precedence as auth_service: user rows present -> team ignored.
    fragment = models_fragment(
        user_aliases=["claude-opus-5-5"], team_aliases=["claude-sonnet-5"], app_models=ACTIVE,
        active_models=ACTIVE, default_model=None,
    )

    assert fragment["availableModels"] == ["claude-opus-5-5"]


def test_app_restriction_alone_still_narrows_the_list():
    # No user/team list, but claude-code may not use every ACTIVE model
    # (model_aliases.allowed_clients) -> list only what this app can use.
    fragment = models_fragment(
        user_aliases=[], team_aliases=[], app_models=["claude-sonnet-5"],
        active_models=ACTIVE, default_model=None,
    )

    assert fragment == {"availableModels": ["claude-sonnet-5"], "enforceAvailableModels": True}


def test_listed_alias_that_is_inactive_or_not_for_this_app_is_dropped():
    fragment = models_fragment(
        user_aliases=["claude-opus-4-8", "claude-sonnet-5"],  # opus-4-8 is INACTIVE
        team_aliases=[], app_models=ACTIVE, active_models=ACTIVE, default_model=None,
    )

    assert fragment["availableModels"] == ["claude-sonnet-5"]


def test_list_follows_app_model_order_not_admin_input_order():
    fragment = models_fragment(
        user_aliases=["claude-sonnet-5", "claude-haiku-4-5-20251001"], team_aliases=[],
        app_models=ACTIVE, active_models=ACTIVE, default_model=None,
    )

    assert fragment["availableModels"] == ["claude-haiku-4-5-20251001", "claude-sonnet-5"]


def test_restriction_that_leaves_nothing_usable_is_an_explicit_empty_list():
    # Mirrors the gateway, which would reject every request: say so with [],
    # never by omitting the key (omitting means "no restriction").
    fragment = models_fragment(
        user_aliases=["claude-opus-4-8"], team_aliases=[], app_models=ACTIVE,
        active_models=ACTIVE, default_model=None,
    )

    assert fragment == {"availableModels": [], "enforceAvailableModels": True}


def test_app_default_model_is_emitted_when_usable():
    fragment = models_fragment(
        user_aliases=[], team_aliases=[], app_models=ACTIVE, active_models=ACTIVE,
        default_model="claude-sonnet-5",
    )

    assert fragment == {"model": "claude-sonnet-5"}


def test_app_default_model_outside_the_allowed_list_is_not_emitted():
    # A default the user may not use would contradict enforceAvailableModels.
    fragment = models_fragment(
        user_aliases=[], team_aliases=["claude-sonnet-5"], app_models=ACTIVE, active_models=ACTIVE,
        default_model="claude-opus-5-5",
    )

    assert "model" not in fragment
    assert fragment["availableModels"] == ["claude-sonnet-5"]

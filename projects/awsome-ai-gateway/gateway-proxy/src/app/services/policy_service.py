"""Claude Code policy fragments served to policy-helper (GET /v1/policy).

A fragment is a slice of Claude Code `managedSettings`. policy-helper lays it over
its built-in baseline and prints the result, which becomes the session's only
managed settings — so everything here must be valid Claude Code settings.

PoC: computed here from the same tables the gateway already enforces. Production
target: admin-api computes it when a policy is saved and the gateway only reads
the stored result (see policy-helper-implementation.md, 6-3).
"""
from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import any_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import User
from app.models.model import ModelAlias, TeamAllowedModel, UserAllowedModel
from app.models.routing import RoutingProfile


def models_fragment(
    *,
    user_aliases: Sequence[str],
    team_aliases: Sequence[str],
    app_models: Sequence[str],
    active_models: Sequence[str],
    default_model: str | None,
) -> dict:
    """Model selection policy for one user.

    - allowed: user list if any, else team list if any, else everything
      (same precedence as auth_service).
    - usable: allowed ∩ app_models, in app_models order. app_models are the
      ACTIVE aliases this client may use (model_aliases.allowed_clients).
    - "no restriction" is expressed by omitting availableModels; Claude Code reads
      `[]` as "nothing allowed", which is only emitted when that is the truth.
    - model: the app's default, only when it is usable.
    """
    allowed = user_aliases or team_aliases or None
    fragment: dict = {}
    if allowed is None and set(app_models) == set(active_models):
        usable = list(app_models)
    else:
        usable = [m for m in app_models if allowed is None or m in allowed]
        fragment["availableModels"] = usable
        fragment["enforceAvailableModels"] = True
    if default_model and default_model in usable:
        fragment["model"] = default_model
    return fragment


async def load_models_inputs(db: AsyncSession, user_id: str, client: str) -> dict | None:
    """Read the inputs of models_fragment fresh from the DB.

    Deliberately not taken from the VK auth context: its allowed_models is a snapshot
    cached at authentication time, and a policy change must show up within one
    refresh interval. Returns None for an unknown or inactive user (read-only — this
    never provisions users, unlike /v1/auth/exchange).
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        return None

    user_aliases = list((await db.execute(
        select(UserAllowedModel.model_alias).where(UserAllowedModel.user_id == user.id)
    )).scalars().all())
    team_aliases: list[str] = []
    if user.team_id:
        team_aliases = list((await db.execute(
            select(TeamAllowedModel.model_alias).where(TeamAllowedModel.team_id == user.team_id)
        )).scalars().all())

    active = (
        select(ModelAlias.alias).where(ModelAlias.status == "ACTIVE").order_by(ModelAlias.alias)
    )
    active_models = list((await db.execute(active)).scalars().all())
    # model_aliases.allowed_clients: NULL = every app, list = whitelist, [] = no app.
    app_models = list((await db.execute(active.where(or_(
        ModelAlias.allowed_clients.is_(None), literal(client) == any_(ModelAlias.allowed_clients),
    )))).scalars().all())

    default_model = (await db.execute(
        select(RoutingProfile.default_model).where(RoutingProfile.client == client)
    )).scalar_one_or_none()

    return {
        "user_aliases": user_aliases,
        "team_aliases": team_aliases,
        "app_models": app_models,
        "active_models": active_models,
        "default_model": default_model,
    }

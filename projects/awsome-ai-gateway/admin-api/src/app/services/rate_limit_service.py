# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import json
import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_logger
from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import Team, User
from app.models.model import ModelAlias, RateLimitConfig, RateLimitScope
from app.repositories.model_repository import RateLimitConfigRepository
from app.repositories.user_repository import UserRepository
from app.schemas.rate_limits import RateLimitConfigItem, RateLimitResponse, RateLimitSetRequest, RateLimitTreeNode

logger = structlog.get_logger()


def _team_display_name(team: Team) -> str:
    """부서가 default가 아니면 '부서명_팀명' 형태로 반환한다.
    예: Claude_NDS_Developers 그룹 → Team.name='Developers', department='NDS' → 'NDS_Developers'"""
    settings = get_settings()
    default_dept_id = uuid.UUID(settings.DEFAULT_DEPT_ID)
    dept = getattr(team, "department", None)
    if dept is not None and team.dept_id != default_dept_id:
        return f"{dept.name}_{team.name}"
    return team.name


class RateLimitService:
    def __init__(self, cache_mgr: CacheInvalidationManager) -> None:
        self._cache_mgr = cache_mgr

    async def set_user_rate_limit(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        data: RateLimitSetRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> RateLimitResponse:
        return await self._set_rate_limit(
            session,
            scope=RateLimitScope.USER,
            scope_id=user_id,
            data=data,
            actor=actor,
            cache_key=f"ratelimit:config:user:{user_id}",
            ip_address=ip_address,
            request_id=request_id,
        )

    async def set_team_rate_limit(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        data: RateLimitSetRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> RateLimitResponse:
        # BR-RL-01: CPM/CPH allowed for USER and TEAM scopes (GLOBAL 제외).
        # 단기 비용 폭주 방어는 개별/팀 레벨에서. GLOBAL은 월 예산 엔진이 커버.

        return await self._set_rate_limit(
            session,
            scope=RateLimitScope.TEAM,
            scope_id=team_id,
            data=data,
            actor=actor,
            cache_key=f"ratelimit:config:team:{team_id}",
            ip_address=ip_address,
            request_id=request_id,
        )

    async def set_global_rate_limit(
        self,
        session: AsyncSession,
        *,
        model_alias: str,
        data: RateLimitSetRequest,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> RateLimitResponse:
        # BR-RL-01: CPM/CPH not allowed for GLOBAL scope
        # (단기 비용 폭주 방어는 USER/TEAM 레벨에서. GLOBAL은 월 예산 엔진이 커버.)
        if data.cpm is not None or data.cph is not None:
            raise ValidationError("CPM/CPH limits are only allowed for USER/TEAM scopes")

        alias_exists = await session.scalar(
            select(ModelAlias.alias).where(ModelAlias.alias == model_alias)
        )
        if alias_exists is None:
            raise NotFoundError("ModelAlias", model_alias)

        return await self._set_rate_limit(
            session,
            scope=RateLimitScope.GLOBAL,
            scope_id=None,
            data=data,
            actor=actor,
            model_alias=model_alias,
            cache_key=f"ratelimit:config:global:{model_alias}",
            ip_address=ip_address,
            request_id=request_id,
        )

    async def get_rate_limit_tree(self, session: AsyncSession) -> list[RateLimitTreeNode]:
        user_repo = UserRepository(session)
        rl_repo = RateLimitConfigRepository(session)

        teams = await user_repo.list_all_teams()
        team_configs = await rl_repo.list_active_by_scope(RateLimitScope.TEAM)
        user_configs = await rl_repo.list_active_by_scope(RateLimitScope.USER)

        team_config_map: dict[str, RateLimitConfig] = {
            str(c.scope_id): c for c in team_configs if c.scope_id
        }
        user_config_map: dict[str, RateLimitConfig] = {
            str(c.scope_id): c for c in user_configs if c.scope_id
        }

        def _make_config(cfg: RateLimitConfig, scope: str) -> RateLimitConfigItem:
            return RateLimitConfigItem(
                target_id=str(cfg.scope_id) if cfg.scope_id else "",
                scope=scope,
                rpm=cfg.rpm_limit,
                tpm=cfg.tpm_limit,
                cpm=cfg.cpm_limit_usd,
                cph=cfg.cph_limit_usd,
            )

        nodes: list[RateLimitTreeNode] = []
        for team in teams:
            team_id = str(team.id)
            team_cfg = team_config_map.get(team_id)

            member_nodes: list[RateLimitTreeNode] = []
            for member in team.members:
                member_id = str(member.id)
                user_cfg = user_config_map.get(member_id)
                inherited_from: str | None = None
                effective_cfg: RateLimitConfigItem | None = None
                if user_cfg:
                    effective_cfg = _make_config(user_cfg, "USER")
                elif team_cfg:
                    effective_cfg = _make_config(team_cfg, "TEAM")
                    inherited_from = team_id
                member_nodes.append(
                    RateLimitTreeNode(
                        id=member_id,
                        label=member.display_name,
                        scope="USER",
                        is_active=member.is_active,
                        config=effective_cfg,
                        children=[],
                        inherited_from=inherited_from,
                    )
                )

            has_active_members = any(m.is_active for m in team.members)
            nodes.append(
                RateLimitTreeNode(
                    id=team_id,
                    label=_team_display_name(team),
                    scope="TEAM",
                    is_active=has_active_members,
                    config=_make_config(team_cfg, "TEAM") if team_cfg else None,
                    children=member_nodes,
                    inherited_from=None,
                )
            )

        return nodes

    async def get_live_usage(
        self, scope: str, scope_id: str, *, window_ms: int = 60_000
    ) -> dict:
        """gateway-proxy 가 적재하는 **실시간 RPM 카운터**(Redis ZSET)를 읽어 현재
        사용량/잔여를 반환(§60.9). 설정값만 보던 RL 화면에 실시간 상태를 더한다.

        proxy 키 규약(gateway-proxy/rate_limit_scope.py:build_rl_key):
          `{{SCOPE:scope_id:model_alias}}:rpm`  (ZSET, member=request_id, score=now_ms)
        현재 사용량 = window(60s) 안의 ZSET 항목 수 = ZCOUNT(key, now-window, +inf).
        모델별로 분리 적재되므로 scope 의 모든 모델 키를 scan 해 합산/모델별 분해.

        429 누적은 usage_logs 에 없다(status enum=SUCCESS/ERROR/TIMEOUT). 실시간
        429 는 proxy 메트릭 영역이므로 여기선 'rpm 현재/한도/잔여'만 정확히 제공.
        fail-soft: Redis 오류/키 없음 → available=false (config 화면은 그대로).
        """
        import time

        redis = self._cache_mgr._redis
        sc = (scope or "").upper()
        if sc not in ("USER", "TEAM", "GLOBAL"):
            return {"available": False, "reason": "invalid scope"}
        # ⚠️ GLOBAL 의 scope_id 는 proxy 가 `*` 로 적는다
        #    (gateway-proxy/rate_limit_scope.py:75 GLOBAL_WILDCARD, :101 scope_id is None → `*`).
        #    예전엔 여기서 `__global__` 을 조립해 SCAN 이 0건 → GLOBAL RPM 이 에러 없이
        #    항상 0/available=true 로 보였다(실제로는 429 를 내는 중일 수 있음).
        #    `[*]` 는 Redis 글롭에서 **리터럴 별표** 문자 클래스라, 임의 scope_id 까지
        #    싸잡지 않으면서 `*` 키만 정확히 잡는다.
        sid = scope_id if sc != "GLOBAL" else "[*]"
        now_ms = int(time.time() * 1000)
        window_start = now_ms - window_ms
        pattern = f"{{{sc}:{sid}:*}}:rpm"  # 해당 scope 의 모든 모델 rpm ZSET

        try:
            per_model: list[dict] = []
            total = 0
            async for key in redis.scan_iter(match=pattern, count=200):
                k = key.decode() if isinstance(key, (bytes, bytearray)) else key
                # 윈도우 내 항목 수(만료분 제외) — proxy 의 ZCARD-after-cleanup 과 동치.
                cnt = await redis.zcount(k, window_start, "+inf")
                cnt = int(cnt or 0)
                if cnt <= 0:
                    continue
                # 키에서 model_alias 추출: {SCOPE:sid:MODEL}:rpm
                inner = k[k.find("{") + 1 : k.find("}")]
                parts = inner.split(":")
                model_alias = parts[2] if len(parts) >= 3 else "*"
                per_model.append({"model_alias": model_alias, "rpm_used": cnt})
                total += cnt
            return {
                "available": True,
                "scope": sc,
                "scope_id": scope_id,
                "window_sec": window_ms // 1000,
                "rpm_used_total": total,
                "by_model": sorted(per_model, key=lambda x: -x["rpm_used"]),
            }
        except Exception as exc:  # noqa: BLE001 — 실시간 조회 실패가 화면을 막지 않게
            logger.warning("rl_live_usage_failed", error=str(exc), scope=sc, scope_id=scope_id)
            return {"available": False, "reason": f"{type(exc).__name__}"}

    # 트렌드 조회의 창/버킷은 고정 조합만 허용 — free-form bucket 을 받으면 임의
    # 비용의 집계가 가능해져 admin-api 풀(pool_size=5+overflow10)을 압박한다.
    _TREND_WINDOWS = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
    _TREND_BUCKETS = {"1h": 60, "6h": 300, "24h": 900, "7d": 3600}
    _TREND_CACHE_TTL_SEC = 60

    async def get_usage_trend(
        self,
        session: AsyncSession,
        scope: str,
        scope_id: str,
        window: str = "24h",
    ) -> dict:
        """USER/TEAM 의 과거 사용량 트렌드 — usage_logs 를 시간 버킷으로 집계.

        한도 설정의 근거 데이터: 버킷당 요청 수(RPM 근거)·토큰 수(TPM 근거,
        cache_read 제외 = compute_tpm_incr 과 동일 기준)·비용(CPH 근거)을 반환.
        idx_usage_logs_{user,team}_time 인덱스로 bounded window 만 스캔하고,
        결과는 60초 Redis 캐시 — 노드 재선택/윈도우 토글 반복이 매번 집계를
        치지 않게 한다. fail-soft: DB/Redis 오류 시 available=false.
        """
        from datetime import datetime, timezone

        from sqlalchemy import text

        sc = (scope or "").upper()
        # GLOBAL 은 usage_logs 의 단일 소유 컬럼에 대응하지 않아 트렌드 대상 아님.
        if sc not in ("USER", "TEAM"):
            return {"available": False, "reason": "invalid scope"}
        win_sec = self._TREND_WINDOWS.get(window)
        if win_sec is None:
            return {"available": False, "reason": "invalid window"}
        try:
            sid = uuid.UUID(scope_id)
        except (ValueError, AttributeError):
            return {"available": False, "reason": "invalid scope_id"}
        bucket_sec = self._TREND_BUCKETS[window]
        col = "user_id" if sc == "USER" else "team_id"
        cache_key = f"trend:rl:{sc}:{scope_id}:{window}"

        try:
            cached = await self._cache_mgr._redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:  # noqa: BLE001 — 캐시 실패는 무시하고 DB 조회로 진행
            pass

        try:
            rows = (
                await session.execute(
                    text(
                        f"""
                        SELECT to_timestamp(
                                   floor(extract(epoch from requested_at) / :bucket) * :bucket
                               ) AS b,
                               count(*) AS req,
                               coalesce(sum(input_tokens + output_tokens
                                            + cache_creation_tokens), 0) AS tok,
                               coalesce(sum(cost_usd), 0) AS cost
                        FROM usage.usage_logs
                        WHERE {col} = :sid
                          AND requested_at >= now() - (:win * interval '1 second')
                        GROUP BY b
                        ORDER BY b
                        """
                    ),
                    {"bucket": bucket_sec, "sid": sid, "win": win_sec},
                )
            ).all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("rl_usage_trend_failed", error=str(exc), scope=sc, scope_id=scope_id)
            return {"available": False, "reason": f"{type(exc).__name__}"}

        by_t = {int(r.b.timestamp()): r for r in rows}
        now_ts = int(datetime.now(timezone.utc).timestamp())
        start_ts = now_ts - win_sec
        start_ts -= start_ts % bucket_sec
        points = []
        t = start_ts
        while t <= now_ts:
            r = by_t.get(t)
            points.append(
                {
                    "t": t,
                    "requests": int(r.req) if r else 0,
                    "tokens": int(r.tok) if r else 0,
                    "cost_usd": float(r.cost) if r else 0.0,
                }
            )
            t += bucket_sec

        result = {
            "available": True,
            "scope": sc,
            "scope_id": scope_id,
            "window_sec": win_sec,
            "bucket_sec": bucket_sec,
            "points": points,
        }
        try:
            await self._cache_mgr._redis.setex(
                cache_key, self._TREND_CACHE_TTL_SEC, json.dumps(result)
            )
        except Exception:  # noqa: BLE001 — 캐시 쓰기 실패는 결과에 영향 없음
            pass
        return result

    async def _set_rate_limit(
        self,
        session: AsyncSession,
        *,
        scope: RateLimitScope,
        scope_id: uuid.UUID | None,
        data: RateLimitSetRequest,
        actor: CurrentUser,
        cache_key: str,
        model_alias: str | None = None,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> RateLimitResponse:
        repo = RateLimitConfigRepository(session)

        config = RateLimitConfig(
            id=uuid.uuid4(),
            scope=scope,
            scope_id=scope_id,
            model_alias=model_alias,
            rpm_limit=data.rpm,
            tpm_limit=data.tpm,
            cpm_limit_usd=data.cpm,
            cph_limit_usd=data.cph,
            is_active=True,
            created_by=actor.user_id,
        )
        await repo.upsert(config)

        # Write config to Redis for Gateway Proxy
        config_json = json.dumps({
            "rpm": data.rpm,
            "tpm": data.tpm,
            "cpm": str(data.cpm) if data.cpm else None,
            "cph": str(data.cph) if data.cph else None,
        })
        await self._cache_mgr._redis.set(cache_key, config_json)

        # Invalidate gateway-proxy's rate-limit policy cache so the new policy
        # takes effect on the next request instead of waiting for the 5-min TTL.
        # Gateway uses keys: rl:config:{SCOPE}:{scope_id_or_NULL}:{model_alias}
        # USER/TEAM with model_alias=None covers all models → wildcard delete.
        sid = str(scope_id) if scope_id is not None else "NULL"
        if model_alias is not None:
            await self._cache_mgr.invalidate(
                [f"rl:config:{scope.value}:{sid}:{model_alias}"],
                session=session,
            )
        else:
            await self._cache_mgr.invalidate_pattern(
                f"rl:config:{scope.value}:{sid}:*",
                session=session,
            )

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="SET_RATE_LIMIT",
            resource_type="RateLimitConfig",
            resource_id=str(config.id),
            changes={"after": {"scope": scope.value, "rpm": data.rpm, "tpm": data.tpm}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return RateLimitResponse(
            scope=scope.value,
            scope_id=str(scope_id) if scope_id else None,
            model_alias=model_alias,
            rpm_limit=config.rpm_limit,
            tpm_limit=config.tpm_limit,
            cpm_limit_usd=config.cpm_limit_usd,
            cph_limit_usd=config.cph_limit_usd,
            is_active=config.is_active,
        )

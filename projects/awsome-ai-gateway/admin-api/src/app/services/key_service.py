# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit_logger
from app.core.auth import CurrentUser
from app.core.cache_invalidation import CacheInvalidationManager
from app.core.encryption import AESEncryptionService
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import KeyStatus, User, VirtualKey
from app.repositories.key_repository import KeyRepository
from app.repositories.model_repository import TeamAllowedModelRepository
from app.repositories.user_allowed_client_repository import UserAllowedClientRepository
from app.repositories.user_allowed_model_repository import UserAllowedModelRepository
from app.repositories.user_repository import UserRepository
from app.schemas.keys import KeyCreateResponse, KeyResponse

logger = structlog.get_logger()

VK_PREFIX = "vk-"
VK_RANDOM_BYTES = 32  # 32 bytes = 64 hex chars → total 67 chars with prefix
VK_AUTH_CACHE_TTL = 300  # gateway-proxy auth_service.VK_CACHE_TTL와 일치
VK_DEFAULT_TTL_HOURS = 24  # 호출자가 expires_at 을 지정하지 않은 경우의 기본값
VK_DEDUP_SECONDS = 5  # 같은 사용자의 연속 issue_key 호출 중복 방지


class KeyService:
    def __init__(
        self,
        encryption: AESEncryptionService,
        cache_mgr: CacheInvalidationManager,
    ) -> None:
        self._encryption = encryption
        self._cache_mgr = cache_mgr

    async def issue_key(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        actor: CurrentUser,
        expires_at: datetime | None = None,
        sso_session_expires_at: datetime | None = None,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
        user: User | None = None,
    ) -> KeyCreateResponse:
        """Issue a new Virtual Key for user_id.

        ``user`` optional: 호출자가 이미 같은 session 에서 조회한 User 객체를 전달하면
        내부 ``get_user(user_id)`` 재조회를 생략. 전달하지 않으면 기존과 동일 동작.
        10k boot storm 부하테스트에서 요청당 DB roundtrip 을 줄여 conn 점유 시간 감소.
        """
        repo = KeyRepository(session)

        # ── Deduplicate rapid issue calls for the same user ──
        # 클라이언트(CLI)가 짧은 시간 안에 같은 사용자로 여러 번 issue_key 호출 시
        # (예: claude-code API key helper 재시도) 기존 ACTIVE 키를 재반환합니다.
        dedup_now = datetime.now(timezone.utc)
        if VK_DEDUP_SECONDS > 0:
            recent_active = await repo.list_active_for_user(user_id)
            if isinstance(recent_active, list):
                for existing in recent_active:
                    age_s = (dedup_now - existing.issued_at).total_seconds()
                    if (
                        age_s <= VK_DEDUP_SECONDS
                        and (sso_session_expires_at is None or existing.expires_at >= sso_session_expires_at)
                    ):
                        logger.info(
                            "key.dedup_returned_recent",
                            user_id=str(user_id),
                            key_id=str(existing.id),
                            age_s=age_s,
                            request_id=request_id,
                        )
                        raw_key = self._encryption.decrypt(existing.key_value_encrypted)
                        return KeyCreateResponse(
                            key_id=str(existing.id),
                            key_prefix=existing.key_prefix,
                            user_id=str(user_id),
                            status=existing.status,
                            created_at=existing.issued_at,
                            expires_at=existing.expires_at,
                            virtual_key=raw_key,
                        )

        # Generate VK: vk- + 32-byte random hex (먼저 생성 — CTE 한 번에 expire+insert)
        raw_key = VK_PREFIX + secrets.token_hex(VK_RANDOM_BYTES)
        key_prefix = raw_key[:11]  # "vk-a3b9c1d2"

        # AES-256-GCM encrypt
        encrypted = self._encryption.encrypt(raw_key)

        if expires_at is None:
            expires_at = datetime.now(timezone.utc) + timedelta(hours=VK_DEFAULT_TTL_HOURS)

        # FR-2.2: cap VK expiry to SSO session expiry if provided
        if sso_session_expires_at and sso_session_expires_at < expires_at:
            logger.info(
                "key.sso_expiry_applied",
                user_id=str(user_id),
                policy_expires_at=expires_at.isoformat(),
                sso_session_expires_at=sso_session_expires_at.isoformat(),
            )
            expires_at = sso_session_expires_at
        else:
            logger.info(
                "key.expiry_set",
                user_id=str(user_id),
                expires_at=expires_at.isoformat(),
                sso_session_expires_at=sso_session_expires_at.isoformat() if sso_session_expires_at else None,
            )

        # issued_at: CTE path uses raw SQL (no ORM refresh), so set explicitly.
        # ORM `repo.create()` path would auto-fill from server_default; here we don't.
        now = datetime.now(timezone.utc)
        vk = VirtualKey(
            id=uuid.uuid4(),
            user_id=user_id,
            key_value_encrypted=encrypted,
            key_prefix=key_prefix,
            status=KeyStatus.ACTIVE,
            expires_at=expires_at,
            issued_at=now,
        )

        # BR-KEY-01 + INSERT 를 단일 CTE 로 (B: round-trip 절감)
        #
        # ⚠️ savepoint + 1회 재시도가 필요한 이유(경쟁조건):
        #    `auth.virtual_keys (user_id) WHERE status='ACTIVE'` 부분 유니크 인덱스가
        #    "사용자당 ACTIVE 키 1개" 를 DB 차원에서 강제한다. 그게 없으면 동시 발급
        #    두 건이 **둘 다 성공해 ACTIVE 키가 2개** 남는다(실 PostgreSQL 16 실측).
        #    CTE 안의 UPDATE/INSERT 는 같은 스냅샷을 보므로 상대 트랜잭션이 넣는 행을
        #    보지 못하고, 그래서 애플리케이션 단독으로는 막을 수 없다.
        #
        #    인덱스를 넣으면 그 경쟁이 유니크 위반으로 드러나는데, 재시도가 없으면
        #    **두 요청이 모두 실패**한다(실측: 최종 ACTIVE 는 기존 키 그대로).
        #    CLI 로그인·OIDC 교환이 그 경로라 사용자에겐 원인 불명의 500 이 된다.
        #    상대가 커밋을 끝낸 뒤 재시도하면 우리 UPDATE 가 그 키를 EXPIRED 로 만들고
        #    INSERT 가 성공한다 — 그래서 1회 재시도로 충분하다.
        #
        #    savepoint(`begin_nested`)로 감싸는 이유: IntegrityError 는 트랜잭션을
        #    abort 상태로 만들어 이후 모든 문장이 거부된다. 이 함수는 commit 책임이
        #    호출자에게 있으므로(docstring), 바깥 트랜잭션을 살려두려면 실패를
        #    savepoint 안에 격리해야 한다.
        expired_count = 0
        for attempt in (1, 2):
            try:
                async with session.begin_nested():
                    expired_count, _ = await repo.expire_and_create(user_id, vk)
                break
            except IntegrityError:
                if attempt == 2:
                    # 두 번째도 실패하면 경쟁이 아니라 다른 제약 위반이다 — 삼키지 않는다.
                    logger.warning(
                        "key.issue_conflict_persisted", user_id=str(user_id)
                    )
                    raise
                logger.info("key.issue_conflict_retry", user_id=str(user_id))
        if expired_count > 0:
            logger.info("key.expired_existing", user_id=str(user_id), count=expired_count)

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        seconds_until_expiry = max(1, int((expires_at - now).total_seconds()))

        # team_allowed_models 스냅샷을 AuthContext 캐시에 주입.
        # 호출자가 user 를 전달했으면 재조회 skip (같은 session identity map 객체 재사용).
        if user is None:
            user_repo = UserRepository(session)
            user = await user_repo.get_user(user_id)
            if user is None:
                raise NotFoundError("User", str(user_id))

        # ── AuthContext 스냅샷 (gateway-proxy 가 캐시 히트 시 그대로 되살린다) ──
        # ⚠️ 이 payload 는 gateway-proxy 가 `AuthContext(**data)` 로 **검증 없이 복원**한다
        #    (gateway-proxy/src/app/services/auth_service.py:66). 즉 여기서 빠뜨린 인가
        #    필드는 캐시가 사는 동안 "제한 없음" 으로 동작하고, 그 사이 gateway 는 DB 를
        #    보지 않으므로 아무도 눈치채지 못한다. 예전엔 두 군데가 새고 있었다:
        #
        #    1) allowed_models 를 team_allowed_models **만으로** 채웠다. gateway 의 정책은
        #       user > team > none 인데(auth_service.py:87-121), user_allowed_models 로 더
        #       좁혀 놓은 사용자가 VK 를 새로 받으면 캐시 TTL 동안 **팀 화이트리스트로
        #       넓어졌다** — 사용자 단위 제한(국가핵심기술)이 통째로 무력화된다.
        #       user_allowed_model_repository 의 주석도 "0행 = 팀 폴백, 그 분기는
        #       key_service / auth_service 가 책임진다" 라고 적어 두었는데 여기가 빠져 있었다.
        #    2) allowed_clients 를 아예 넣지 않았다 → AuthContext 기본값 None = 전 클라이언트
        #       허용이라 ClientAuthorizationMiddleware(403)가 통과시킨다. VK + allowed_clients
        #       는 ARCHITECTURE.md 가 명시한 유일한 클라이언트 인가 축이다(UA 는 스푸핑 가능).
        #
        #    그래서 gateway 와 같은 우선순위·같은 필드로 계산한다. 정합성은
        #    tests/regression 의 AuthContext 필드 대조 테스트가 못 박는다.
        acl_snapshot_ok = True
        allowed_models: list[str] | None = None
        allowed_clients: list[str] | None = None
        try:
            uam_repo = UserAllowedModelRepository(session)
            user_aliases = await uam_repo.list_by_user(user_id)
            if user_aliases:
                # user override 존재 → 팀 정책은 보지 않는다(gateway 와 동일).
                allowed_models = user_aliases
            elif user.team_id is not None:
                tam_repo = TeamAllowedModelRepository(session)
                team_aliases = await tam_repo.list_by_team(user.team_id)
                # 엔트리 0개 → None (전체 허용). 엔트리 존재 → 화이트리스트.
                allowed_models = team_aliases if team_aliases else None

            uac_repo = UserAllowedClientRepository(session)
            client_rows = await uac_repo.list_by_user(user_id)
            allowed_clients = client_rows if client_rows else None
        except Exception:
            # 스냅샷은 콜드캐시 DB 왕복을 아끼기 위한 **최적화**일 뿐이다. 정책을 못 읽었으면
            # 넓은 스냅샷을 심는 대신 아예 심지 않는다 — gateway 가 첫 요청에서 직접 조회하고
            # 자기 fail-closed 규칙(auth_service.py:99-107)을 적용한다. VK 발급은 살린다.
            acl_snapshot_ok = False
            logger.warning(
                "key.acl_snapshot_skipped",
                user_id=str(user_id),
                reason="allowed_models/allowed_clients 조회 실패 — gateway 가 직접 조회한다",
                exc_info=True,
            )

        auth_context_payload = {
            "user_id": str(user.id),
            "team_id": str(user.team_id) if user.team_id else "",
            "dept_id": "",
            "roles": ["USER"],
            "auth_type": "VIRTUAL_KEY",
            "key_id": None,
            "allowed_models": allowed_models,
            "allowed_clients": allowed_clients,
            "sso_subject": user.sso_subject,
        }
        auth_cache_ttl = min(VK_AUTH_CACHE_TTL, seconds_until_expiry)

        # Redis 작업 3건 (VK lookup, auth context, team reverse index) 을 pipeline 으로
        # 묶어 round-trip 절감. 각 key 가 독립적이라 순서/원자성 무관.
        redis = self._cache_mgr._redis
        pipe = redis.pipeline(transaction=False)
        pipe.setex(f"key:vk:{key_hash}", seconds_until_expiry, f"{user_id}")
        if acl_snapshot_ok:
            pipe.setex(
                f"key:cache:vk:{key_hash}",
                auth_cache_ttl,
                json.dumps(auth_context_payload),
            )
        if user.team_id is not None:
            pipe.sadd(f"team:vk_hashes:{user.team_id}", key_hash)
        await pipe.execute()

        # Audit log
        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="CREATE_KEY",
            resource_type="VirtualKey",
            resource_id=str(vk.id),
            changes={"after": {"user_id": str(user_id), "key_prefix": key_prefix}},
            ip_address=ip_address,
            request_id=request_id,
        )

        return KeyCreateResponse(
            key_id=str(vk.id),
            key_prefix=key_prefix,
            user_id=str(user_id),
            status=vk.status,
            created_at=vk.issued_at,
            expires_at=vk.expires_at,
            virtual_key=raw_key,
        )

    async def _publish_key_revoked(
        self, vk: VirtualKey, actor: CurrentUser
    ) -> None:
        """VK 폐기 시 notification-worker에 key_revoked 이벤트 발행."""
        redis = self._cache_mgr._redis
        if redis is None:
            return
        event = {
            "event_id": str(uuid.uuid4()),
            "type": "key_revoked",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "admin-api",
            "payload": {
                "user_id": str(vk.user_id),
                "key_id": str(vk.id),
                "key_prefix": vk.key_prefix,
                "revoked_by": actor.role.value,
                "reason": None,
            },
        }
        try:
            await redis.publish("notifications:key", json.dumps(event, default=str))
        except Exception:
            logger.warning("key_revoked.publish_failed", exc_info=True)

    async def revoke_key(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> None:
        repo = KeyRepository(session)
        vk = await repo.get_by_id(key_id)
        if vk is None:
            raise NotFoundError("VirtualKey", str(key_id))
        if vk.status != KeyStatus.ACTIVE:
            raise ValidationError(
                f"Only ACTIVE virtual keys can be revoked (status: {vk.status.value})"
            )

        original_status = vk.status
        await repo.revoke(key_id, actor.user_id)

        # Invalidate Redis cache
        # We need the hash of the raw key, but we only have encrypted.
        # Decrypt to get raw key, then hash it.
        raw_key = self._encryption.decrypt(vk.key_value_encrypted)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        await self._cache_mgr.invalidate(
            [f"key:vk:{key_hash}", f"key:cache:vk:{key_hash}"],
            session=session,
        )

        # Remove from team VK hash reverse index (FR-2.6)
        user_repo = UserRepository(session)
        user = await user_repo.get_user(vk.user_id)
        if user is not None and user.team_id is not None:
            try:
                await self._cache_mgr._redis.srem(
                    f"team:vk_hashes:{user.team_id}", key_hash
                )
            except Exception:
                logger.warning("vk_reverse_index.srem_failed", user_id=str(vk.user_id), exc_info=True)

        # BR-KEY-04: Audit log for revocation
        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="REVOKE_KEY",
            resource_type="VirtualKey",
            resource_id=str(key_id),
            changes={
                "before": {"status": original_status.value},
                "after": {"status": KeyStatus.REVOKED.value},
            },
            ip_address=ip_address,
            request_id=request_id,
        )

        # notification-worker 에 key_revoked 발행
        await self._publish_key_revoked(vk, actor)

    async def list_keys(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
        status: KeyStatus | None = None,
        email: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[list[KeyResponse], bool]:
        from sqlalchemy import select

        from app.models.auth import User

        repo = KeyRepository(session)
        cursor_uuid = uuid.UUID(cursor) if cursor else None
        keys = await repo.list_keys(
            user_id=user_id,
            team_id=team_id,
            status=status,
            email=email,
            cursor=cursor_uuid,
            limit=limit + 1,
        )
        has_more = len(keys) > limit
        if has_more:
            keys = keys[:limit]

        email_by_user: dict[uuid.UUID, str] = {}
        if keys:
            uids = {vk.user_id for vk in keys}
            rows = await session.execute(
                select(User.id, User.email).where(User.id.in_(uids))
            )
            email_by_user = {uid: em for uid, em in rows.all()}

        items = [
            KeyResponse(
                key_id=str(vk.id),
                key_prefix=vk.key_prefix,
                user_id=str(vk.user_id),
                user_email=email_by_user.get(vk.user_id),
                status=vk.status,
                issued_at=vk.issued_at,
                expires_at=vk.expires_at,
                last_used_at=vk.last_used_at,
                created_at=vk.created_at,
            )
            for vk in keys
        ]
        return items, has_more

    async def count_keys(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID | None = None,
        team_id: uuid.UUID | None = None,
        status: KeyStatus | None = None,
        email: str | None = None,
    ) -> int:
        repo = KeyRepository(session)
        return await repo.count_keys(
            user_id=user_id, team_id=team_id, status=status, email=email
        )

    async def force_reauth_team(
        self,
        session: AsyncSession,
        *,
        team_id: uuid.UUID,
        actor: CurrentUser,
        ip_address: str = "0.0.0.0",
        request_id: str = "",
    ) -> int:
        """팀 멤버 전원의 ACTIVE VK 를 일괄 revoke.

        사용 시나리오:
        - 오프보딩/보안 사고 등 즉시 차단이 필요할 때
        - Cognito group 변경을 1h TTL 자연 만료보다 빠르게 반영하고 싶을 때

        사용자 측 영향: 다음 호출이 401 로 차단됨. Claude Code 재실행 시
        vk-cache 만료 → apiKeyHelper exchange → 새 VK (현재 Cognito groups 기준)
        발급 → 자동 복구. 사용자에게 재실행 안내가 필요함을 UI 에서 명시.

        Returns:
            revoked VK 개수.
        """
        repo = KeyRepository(session)
        # 팀 멤버의 ACTIVE VK 만 대상 (이미 REVOKED/EXPIRED 는 무의미).
        # limit=1000: 단일 팀 멤버 수 상한 가정. 넘으면 FK 로그 필요 (MVP 단계).
        keys = await repo.list_keys(
            team_id=team_id, status=KeyStatus.ACTIVE, cursor=None, limit=1000
        )

        revoked = 0
        for vk in keys:
            try:
                await self.revoke_key(
                    session,
                    key_id=vk.id,
                    actor=actor,
                    ip_address=ip_address,
                    request_id=request_id,
                )
                revoked += 1
            except Exception:
                logger.exception(
                    "force_reauth_team.revoke_failed",
                    team_id=str(team_id),
                    key_id=str(vk.id),
                )
                # 한 건 실패해도 나머지 계속 처리

        await audit_logger.log(
            session,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            action="FORCE_REAUTH_TEAM",
            resource_type="Team",
            resource_id=str(team_id),
            changes={"after": {"revoked_count": revoked}},
            ip_address=ip_address,
            request_id=request_id,
        )

        logger.info(
            "force_reauth_team.completed",
            team_id=str(team_id),
            revoked_count=revoked,
        )
        return revoked

    async def list_active_vk_hashes_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
    ) -> list[str]:
        """Return SHA256 hashes of all non-revoked VKs owned by user_id.

        Mirrors the decrypt-then-hash pattern used by `revoke_key` (line 182-183)
        since `auth.virtual_keys` does not store the hash directly.
        """
        repo = KeyRepository(session)
        rows = await repo.list_active_for_user(user_id)
        hashes: list[str] = []
        for vk in rows:
            try:
                raw = self._encryption.decrypt(vk.key_value_encrypted)
                hashes.append(hashlib.sha256(raw.encode()).hexdigest())
            except Exception:
                logger.warning(
                    "vk_hash_derive_failed", vk_id=str(vk.id), user_id=str(user_id),
                    exc_info=True,
                )
                continue
        return hashes


"""Business logic for the roles domain.

Anti-escalation rule (one of the few hard invariants in the product):
A user can never grant a role of *higher* privilege than their own.
"Higher privilege" means lower numeric level (1 = developer, 4 = seller).

Effective level used for the check:
- is_developer  → 1
- is_administrator → 2
- else: min level across the user's active assignments in the active tenant,
  or 4 ("seller") as a safe ceiling if they have no assignments.

Permission cache lives in Redis: key `auth:perms:{user_id}:{tenant_id}`,
TTL 5 minutes. Any change that affects a user's permissions
(create/delete assignment, role swap) calls `invalidate_user_perms` so
the next request recomputes from the DB.
"""

from __future__ import annotations

import json
from uuid import UUID

import structlog
from redis.asyncio import Redis

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.domains.auth.models import AppUser
from app.domains.roles.models import Permission, Role, UserAssignment
from app.domains.roles.repository import RolesRepository

logger = structlog.get_logger("roles.service")

PERMS_CACHE_TTL_SECONDS = 5 * 60
PERMS_CACHE_PREFIX = "auth:perms"


def perms_cache_key(user_id: UUID, tenant_id: UUID) -> str:
    return f"{PERMS_CACHE_PREFIX}:{user_id}:{tenant_id}"


class RolesService:
    def __init__(self, repo: RolesRepository, redis: Redis | None = None) -> None:
        self.repo = repo
        self.redis = redis

    # -------------------------------------------------------------------------
    # Catalogue reads
    # -------------------------------------------------------------------------

    async def list_permissions(self) -> list[Permission]:
        return await self.repo.list_permissions()

    async def list_roles_with_permissions(self) -> list[tuple[Role, list[str]]]:
        roles = await self.repo.list_roles()
        out: list[tuple[Role, list[str]]] = []
        for role in roles:
            codes = await self.repo.get_role_permissions(role.id)
            out.append((role, codes))
        return out

    # -------------------------------------------------------------------------
    # Users in tenant
    # -------------------------------------------------------------------------

    async def list_users(
        self, tenant_id: UUID
    ) -> list[tuple[AppUser, list[UserAssignment]]]:
        users = await self.repo.list_users_for_tenant(tenant_id)
        out: list[tuple[AppUser, list[UserAssignment]]] = []
        for user in users:
            assignments = await self.repo.list_assignments_for_user(user.id, tenant_id=tenant_id)
            out.append((user, assignments))
        return out

    # -------------------------------------------------------------------------
    # Invite + assignments — anti-escalation enforced here
    # -------------------------------------------------------------------------

    async def invite_user(
        self,
        *,
        actor_level: int,
        actor_id: UUID,
        tenant_id: UUID,
        email: str,
        full_name: str,
        role_id: UUID,
        branch_id: UUID | None,
        password_required: bool,
    ) -> tuple[AppUser, UserAssignment, bool]:
        target_role = await self.repo.get_role(role_id)
        if target_role is None or not target_role.is_active:
            raise NotFoundError("Role not found")
        self._assert_can_assign(actor_level=actor_level, target_level=target_role.level)

        user = await self.repo.get_user_by_email(email)
        if user is None:
            user = await self.repo.insert_user(
                email=email,
                full_name=full_name,
                home_tenant_id=tenant_id,
                status="invited",
            )
            first_invite = True
        else:
            first_invite = False

        # One assignment per (user, tenant, branch). If an *active* one is
        # already there → conflict. If an *inactive* one exists (e.g. user
        # was previously offboarded) → reactivate it with the new role.
        existing = await self.repo.list_assignments_for_user(user.id, tenant_id=tenant_id)
        assignment = None
        for a in existing:
            if a.branch_id == branch_id:
                if a.is_active:
                    raise ConflictError("User already has an active assignment for this branch")
                assignment = await self.repo.reactivate_assignment(
                    a.id,
                    role_id=role_id,
                    password_required=password_required,
                    updated_by=actor_id,
                )
                break

        if assignment is None:
            assignment = await self.repo.insert_assignment(
                user_id=user.id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                role_id=role_id,
                password_required=password_required,
                created_by=actor_id,
            )
        await self.invalidate_user_perms(user.id, tenant_id)

        if first_invite:
            # Lazy import — Celery task module pulls celery_app which imports
            # config at module load; keep this off the cold-start path.
            from app.tasks.roles import send_invite_email

            send_invite_email.delay(email, str(tenant_id))

        return user, assignment, first_invite

    async def assign_role(
        self,
        *,
        actor_level: int,
        actor_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
        role_id: UUID,
        branch_id: UUID | None,
        password_required: bool,
    ) -> UserAssignment:
        target_role = await self.repo.get_role(role_id)
        if target_role is None or not target_role.is_active:
            raise NotFoundError("Role not found")
        self._assert_can_assign(actor_level=actor_level, target_level=target_role.level)

        target_user = await self.repo.get_user(target_user_id)
        if target_user is None:
            raise NotFoundError("User not found")

        existing = await self.repo.list_assignments_for_user(target_user_id, tenant_id=tenant_id)
        assignment = None
        for a in existing:
            if a.branch_id == branch_id:
                if a.is_active:
                    raise ConflictError("User already has an active assignment for this branch")
                assignment = await self.repo.reactivate_assignment(
                    a.id,
                    role_id=role_id,
                    password_required=password_required,
                    updated_by=actor_id,
                )
                break

        if assignment is None:
            assignment = await self.repo.insert_assignment(
                user_id=target_user_id,
                tenant_id=tenant_id,
                branch_id=branch_id,
                role_id=role_id,
                password_required=password_required,
                created_by=actor_id,
            )
        await self.invalidate_user_perms(target_user_id, tenant_id)
        return assignment

    async def revoke_assignment(
        self,
        *,
        actor_level: int,
        tenant_id: UUID,
        target_user_id: UUID,
        assignment_id: UUID,
    ) -> None:
        assignment = await self.repo.get_assignment(assignment_id)
        if assignment is None or assignment.user_id != target_user_id:
            raise NotFoundError("Assignment not found")
        if assignment.tenant_id != tenant_id:
            raise NotFoundError("Assignment not found")

        # Anti-escalation: a user can only revoke assignments whose role is
        # at-or-below their own level (otherwise an owner could fire admins).
        role = await self.repo.get_role(assignment.role_id)
        if role is not None:
            self._assert_can_assign(actor_level=actor_level, target_level=role.level)

        await self.repo.deactivate_assignment(assignment_id)
        await self.invalidate_user_perms(target_user_id, tenant_id)

    async def block_user(self, *, actor_level: int, tenant_id: UUID, target_user_id: UUID) -> None:
        # Anti-escalation: block requires the actor's level <= every
        # assignment's role level in this tenant.
        assignments = await self.repo.list_assignments_for_user(target_user_id, tenant_id=tenant_id)
        for a in assignments:
            if a.is_active:
                role = await self.repo.get_role(a.role_id)
                if role is not None:
                    self._assert_can_assign(actor_level=actor_level, target_level=role.level)
        user = await self.repo.get_user(target_user_id)
        if user is None:
            raise NotFoundError("User not found")
        from app.core.time import utc_now

        await self.repo.update_user(user, status="blocked", blocked_at=utc_now())
        await self.invalidate_user_perms(target_user_id, tenant_id)

    async def soft_delete_user(
        self, *, actor_level: int, tenant_id: UUID, target_user_id: UUID
    ) -> None:
        assignments = await self.repo.list_assignments_for_user(target_user_id, tenant_id=tenant_id)
        for a in assignments:
            if a.is_active:
                role = await self.repo.get_role(a.role_id)
                if role is not None:
                    self._assert_can_assign(actor_level=actor_level, target_level=role.level)
        user = await self.repo.get_user(target_user_id)
        if user is None:
            raise NotFoundError("User not found")
        from app.core.time import utc_now

        await self.repo.update_user(user, status="archived", archived_at=utc_now())
        await self.invalidate_user_perms(target_user_id, tenant_id)

    async def update_user_profile(
        self, *, tenant_id: UUID, target_user_id: UUID, fields: dict[str, object]
    ) -> AppUser:
        # Visibility: only users with at least one assignment in this tenant.
        assignments = await self.repo.list_assignments_for_user(target_user_id, tenant_id=tenant_id)
        if not assignments:
            raise NotFoundError("User not found in this tenant")
        user = await self.repo.get_user(target_user_id)
        if user is None:
            raise NotFoundError("User not found")
        return await self.repo.update_user(user, **fields)

    # -------------------------------------------------------------------------
    # Effective permissions (Redis cache)
    # -------------------------------------------------------------------------

    async def get_effective_permissions(self, user_id: UUID, tenant_id: UUID) -> set[str]:
        """Returns the cached perm set or recomputes + caches it."""
        if self.redis is not None:
            cached = await self.redis.get(perms_cache_key(user_id, tenant_id))
            if cached:
                try:
                    return set(json.loads(cached))
                except (ValueError, TypeError):
                    pass  # fall through to recompute

        perms = await self.repo.effective_permissions(user_id, tenant_id)

        if self.redis is not None:
            await self.redis.set(
                perms_cache_key(user_id, tenant_id),
                json.dumps(sorted(perms)),
                ex=PERMS_CACHE_TTL_SECONDS,
            )
        return perms

    async def invalidate_user_perms(self, user_id: UUID, tenant_id: UUID) -> None:
        if self.redis is None:
            return
        await self.redis.delete(perms_cache_key(user_id, tenant_id))
        logger.info("perms_cache_invalidated", user_id=str(user_id), tenant_id=str(tenant_id))

    async def invalidate_user_perms_all_tenants(self, user_id: UUID) -> None:
        """Drop every cached entry for this user across all tenants (used at
        logout)."""
        if self.redis is None:
            return
        pattern = f"{PERMS_CACHE_PREFIX}:{user_id}:*"
        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)
        logger.info("perms_cache_purged_for_user", user_id=str(user_id))

    # -------------------------------------------------------------------------
    # Anti-escalation helper
    # -------------------------------------------------------------------------

    @staticmethod
    def _assert_can_assign(*, actor_level: int, target_level: int) -> None:
        if target_level < actor_level:
            raise BusinessRuleError(
                "Cannot assign role of higher privilege than your own",
                details={"actor_level": actor_level, "target_level": target_level},
            )

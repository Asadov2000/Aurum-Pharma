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

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domains.auth.models import AppUser
from app.domains.roles.models import Permission, Role, RoleTemplate, UserAssignment
from app.domains.roles.repository import RolesRepository

logger = structlog.get_logger("roles.service")

PERMS_CACHE_TTL_SECONDS = 5 * 60
PERMS_CACHE_PREFIX = "auth:perms"

# Owner provisioning: a tenant's «Владелец» role is instantiated from the global
# owner template (seeded in 0019, slug added in 0023). Looked up by the stable
# slug, not the display name. Level 3 = owner tier (1=dev … 4=seller).
OWNER_TEMPLATE_SLUG = "owner"
CASHIER_TEMPLATE_SLUG = "cashier"
OWNER_ROLE_NAME = "Владелец"
OWNER_ROLE_LEVEL = 3


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

    async def list_roles_with_permissions(
        self, tenant_id: UUID | None = None
    ) -> list[tuple[Role, list[str]]]:
        roles = await self.repo.list_roles(tenant_id=tenant_id)
        perms = await self.repo.permissions_for_roles([r.id for r in roles])
        return [(role, perms.get(role.id, [])) for role in roles]

    async def list_templates_with_permissions(self) -> list[tuple[RoleTemplate, list[str]]]:
        templates = await self.repo.list_templates()
        perms = await self.repo.permissions_for_templates([t.id for t in templates])
        return [(template, perms.get(template.id, [])) for template in templates]

    # -------------------------------------------------------------------------
    # Owner provisioning (support-level: dev/admin onboards a new pharmacy)
    # -------------------------------------------------------------------------

    async def provision_owner(
        self,
        *,
        tenant_id: UUID,
        email: str,
        full_name: str,
        actor_id: UUID | None = None,
    ) -> tuple[AppUser, Role]:
        """Create the first owner of a tenant and give them a tenant «Владелец»
        role instantiated from the global template. Support-level operation —
        the anti-escalation subset check is intentionally bypassed (the source is
        the trusted system template), so this goes straight through the repo, not
        create_role. Caller runs it inside one transaction (all-or-nothing)."""
        if await self.repo.get_user_by_email(email) is not None:
            raise ConflictError("Пользователь с таким email уже существует")

        user = await self.repo.insert_user(
            email=email,
            full_name=full_name,
            home_tenant_id=tenant_id,
            is_developer=False,
            is_administrator=False,
            status="active",
        )
        role = await self._ensure_tenant_owner_role(tenant_id, actor_id)
        await self.repo.insert_assignment(
            user_id=user.id,
            tenant_id=tenant_id,
            branch_id=None,
            role_id=role.id,
            password_required=False,
            created_by=actor_id,
        )
        logger.info("owner_provisioned", tenant_id=str(tenant_id), user_id=str(user.id))
        return user, role

    async def _ensure_tenant_owner_role(self, tenant_id: UUID, actor_id: UUID | None) -> Role:
        """Reuse the tenant's «Владелец» role if present, else instantiate it from
        the global template (full permission set copied verbatim)."""
        existing = await self.repo.get_role_by_name(OWNER_ROLE_NAME, tenant_id=tenant_id)
        if existing is not None:
            return existing
        template = await self.repo.get_template_by_slug(OWNER_TEMPLATE_SLUG)
        if template is None:
            raise NotFoundError("Шаблон роли «Владелец» не найден")
        codes = await self.repo.get_template_permissions(template.id)
        role = await self.repo.insert_role(
            tenant_id=tenant_id,
            name=OWNER_ROLE_NAME,
            description=template.description,
            level=OWNER_ROLE_LEVEL,
            is_system=False,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.repo.set_role_permissions(role.id, codes)
        return role

    # -------------------------------------------------------------------------
    # Role builder — create / edit custom (tenant) roles
    # -------------------------------------------------------------------------

    async def create_role(
        self,
        *,
        actor_level: int,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_is_support: bool,
        tenant_id: UUID,
        name: str,
        description: str | None,
        level: int,
        permission_codes: list[str],
    ) -> tuple[Role, list[str]]:
        self._assert_can_define_role(actor_level=actor_level, target_level=level)
        codes = await self._validated_role_permissions(
            actor_permissions=actor_permissions,
            actor_is_support=actor_is_support,
            target_level=level,
            requested=permission_codes,
        )
        if await self.repo.get_role_by_name(name, tenant_id=tenant_id) is not None:
            raise ConflictError("A role with this name already exists")

        role = await self.repo.insert_role(
            tenant_id=tenant_id,
            name=name,
            description=description,
            level=level,
            is_system=False,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.repo.set_role_permissions(role.id, codes)
        return role, sorted(codes)

    async def update_role(
        self,
        *,
        actor_level: int,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_is_support: bool,
        tenant_id: UUID,
        role_id: UUID,
        name: str | None,
        description: str | None,
        level: int | None,
        permission_codes: list[str] | None,
    ) -> tuple[Role, list[str]]:
        role = await self.repo.get_role(role_id)
        if role is None:
            raise NotFoundError("Role not found")
        if role.is_system:
            # Explicit 403 first — system roles are visible to every tenant
            # (tenant_id NULL), so this guard must precede the ownership check.
            raise PermissionDeniedError("System roles cannot be modified")
        if role.tenant_id != tenant_id:
            raise NotFoundError("Role not found")  # another tenant's role — hide it

        # The actor must outrank the role as it currently stands, and — if the
        # level is being changed — the new level too.
        should_invalidate = level is not None or permission_codes is not None
        affected_user_ids = (
            await self.repo.active_user_ids_for_role(role.id, tenant_id=tenant_id)
            if should_invalidate
            else []
        )

        self._assert_can_define_role(actor_level=actor_level, target_level=role.level)
        if level is not None:
            self._assert_can_define_role(actor_level=actor_level, target_level=level)

        if name is not None and name != role.name:
            clash = await self.repo.get_role_by_name(name, tenant_id=tenant_id)
            if clash is not None and clash.id != role.id:
                raise ConflictError("A role with this name already exists")

        fields: dict[str, object] = {"updated_by": actor_id}
        if name is not None:
            fields["name"] = name
        if description is not None:
            fields["description"] = description
        if level is not None:
            fields["level"] = level
        role = await self.repo.update_role(role, **fields)

        target_level = level if level is not None else role.level
        if level is not None and permission_codes is None:
            await self._assert_permissions_fit_role_level(
                await self.repo.get_role_permissions(role.id),
                target_level=target_level,
            )

        if permission_codes is not None:
            codes = await self._validated_role_permissions(
                actor_permissions=actor_permissions,
                actor_is_support=actor_is_support,
                target_level=target_level,
                requested=permission_codes,
            )
            await self.repo.set_role_permissions(role.id, codes)

        if affected_user_ids:
            await self.invalidate_users_perms(affected_user_ids, tenant_id)

        return role, await self.repo.get_role_permissions(role.id)

    async def _validated_role_permissions(
        self,
        *,
        actor_permissions: set[str],
        actor_is_support: bool,
        target_level: int,
        requested: list[str],
    ) -> list[str]:
        """Dedupe + validate the codes a role is being given: every code must
        exist and be active, must fit the target role's level, and (unless the
        actor is dev/admin) must be one the actor already holds — you cannot
        grant reach you don't have yourself."""
        codes = list(dict.fromkeys(requested))  # de-dupe, keep order
        if codes:
            levels = await self.repo.active_permission_levels(codes)
            unknown = [c for c in codes if c not in levels]
            if unknown:
                raise ValidationError(
                    "Unknown or inactive permission codes",
                    details={"permissions": unknown},
                )
            too_strong = sorted(
                code for code, min_level in levels.items() if min_level < target_level
            )
            if too_strong:
                raise PermissionDeniedError(
                    "Cannot grant permissions above the role level",
                    details={"permissions": too_strong, "role_level": target_level},
                )
        if not actor_is_support:
            extra = sorted(set(codes) - actor_permissions)
            if extra:
                raise PermissionDeniedError(
                    "Cannot grant permissions you do not hold yourself",
                    details={"permissions": extra},
                )
        return codes

    async def _assert_permissions_fit_role_level(
        self, codes: list[str], *, target_level: int
    ) -> None:
        if not codes:
            return
        levels = await self.repo.active_permission_levels(codes)
        too_strong = sorted(code for code, min_level in levels.items() if min_level < target_level)
        if too_strong:
            raise PermissionDeniedError(
                "Cannot keep permissions above the role level",
                details={"permissions": too_strong, "role_level": target_level},
            )

    # -------------------------------------------------------------------------
    # Users in tenant
    # -------------------------------------------------------------------------

    async def list_users(
        self, tenant_id: UUID, *, page: int = 1, page_size: int = 50
    ) -> tuple[list[tuple[AppUser, list[UserAssignment]]], int]:
        total = await self.repo.count_users_for_tenant(tenant_id)
        users = await self.repo.list_users_for_tenant(
            tenant_id, limit=page_size, offset=(page - 1) * page_size
        )
        by_user: dict[UUID, list[UserAssignment]] = {}
        for a in await self.repo.assignments_for_users([u.id for u in users], tenant_id=tenant_id):
            by_user.setdefault(a.user_id, []).append(a)
        return [(u, by_user.get(u.id, [])) for u in users], total

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
        self._assert_role_belongs_to_tenant(target_role, tenant_id)
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
        self._assert_role_belongs_to_tenant(target_role, tenant_id)
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

    async def invalidate_users_perms(self, user_ids: list[UUID], tenant_id: UUID) -> None:
        if self.redis is None or not user_ids:
            return
        keys = [perms_cache_key(user_id, tenant_id) for user_id in set(user_ids)]
        await self.redis.delete(*keys)
        logger.info(
            "perms_cache_invalidated_for_role_users",
            tenant_id=str(tenant_id),
            users=len(keys),
        )

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

    @staticmethod
    def _assert_role_belongs_to_tenant(role: Role, tenant_id: UUID) -> None:
        if role.tenant_id is not None and role.tenant_id != tenant_id:
            raise NotFoundError("Role not found")

    @staticmethod
    def _assert_can_define_role(*, actor_level: int, target_level: int) -> None:
        """Defining (creating / editing) a custom role is stricter than
        assigning one: the role must be *strictly* weaker than the actor (a
        higher numeric level; 1 = developer … 4 = seller). Equal-or-stronger
        would let an actor mint a role with their own — or greater — reach, so
        it is refused with a 403."""
        if target_level <= actor_level:
            raise PermissionDeniedError(
                "Cannot create or edit a role at or above your own level",
                details={"actor_level": actor_level, "target_level": target_level},
            )

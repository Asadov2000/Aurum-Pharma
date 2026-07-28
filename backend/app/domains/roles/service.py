"""Business rules for scoped tenant membership and delegated authorization."""

from __future__ import annotations

from collections.abc import Mapping
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
from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.roles.models import (
    Permission,
    Role,
    RoleTemplate,
    TenantMembership,
    TenantOwnership,
    UserAssignment,
)
from app.domains.roles.repository import AuthorizationSnapshot, DirectoryUser, RolesRepository

logger = structlog.get_logger("roles.service")

PERMS_CACHE_PREFIX = "auth:perms"
OWNER_TEMPLATE_SLUG = "owner"
OWNER_ROLE_NAME = "Владелец"
OWNER_ROLE_LEVEL = 3
CUSTOM_ROLE_LEGACY_LEVEL = 4
RESERVED_ROLE_NAMES = {
    "administrator",
    "developer",
    "owner",
    "администратор",
    "владелец",
    "разработчик",
}


def perms_cache_key(user_id: UUID, tenant_id: UUID) -> str:
    return f"{PERMS_CACHE_PREFIX}:{user_id}:{tenant_id}"


class RolesService:
    def __init__(self, repo: RolesRepository, redis: Redis | None = None) -> None:
        self.repo = repo
        self.redis = redis

    # -------------------------------------------------------------------------
    # Delegable catalogue
    # -------------------------------------------------------------------------

    async def list_permissions(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> list[Permission]:
        return await self._delegation_catalog(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )

    async def list_roles_with_permissions(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> list[tuple[Role, list[str], bool]]:
        roles = [
            role
            for role in await self.repo.list_roles(tenant_id=tenant_id)
            if role.tenant_id == tenant_id and not role.is_system and not role.is_protected
        ]
        permissions = await self.repo.permissions_for_roles([role.id for role in roles])
        catalog = await self._delegation_catalog(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        allowed = {permission.code for permission in catalog}
        result: list[tuple[Role, list[str], bool]] = []
        for role in roles:
            role_codes = set(permissions.get(role.id, []))
            visible_codes = sorted(role_codes & allowed)
            result.append((role, visible_codes, bool(role_codes - allowed)))
        return result

    async def list_templates_with_permissions(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> list[tuple[RoleTemplate, list[str]]]:
        catalog = await self._delegation_catalog(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        allowed = {permission.code for permission in catalog}
        templates = await self.repo.list_templates()
        permissions = await self.repo.permissions_for_templates(
            [template.id for template in templates]
        )
        return [
            (
                template,
                sorted(set(permissions.get(template.id, [])) & allowed),
            )
            for template in templates
        ]

    async def _delegation_catalog(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> list[Permission]:
        permissions = [
            permission
            for permission in await self.repo.list_permissions()
            if permission.is_active and permission.target_role_type == "tenant"
        ]
        if actor_is_developer:
            return [permission for permission in permissions if permission.developer_delegable]
        if actor_is_administrator:
            return [permission for permission in permissions if permission.administrator_delegable]
        if not await self.repo.has_active_ownership(
            tenant_id=tenant_id,
            user_id=actor_id,
        ):
            raise PermissionDeniedError("Active tenant ownership is required")
        return [
            permission
            for permission in permissions
            if permission.owner_delegable and permission.code in actor_permissions
        ]

    async def _validated_role_permissions(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
        requested: list[str],
    ) -> list[str]:
        codes = list(dict.fromkeys(requested))
        all_permissions = await self.repo.list_permissions()
        known = {permission.code for permission in all_permissions}
        unknown = sorted(set(codes) - known)
        if unknown:
            raise ValidationError(
                "Unknown permission codes",
                details={"permissions": unknown},
            )

        catalog = await self._delegation_catalog(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        allowed = {permission.code for permission in catalog}
        unavailable = sorted(set(codes) - allowed)
        if unavailable:
            raise PermissionDeniedError(
                "Permissions are outside the delegation envelope",
                details={"permissions": unavailable},
            )

        return sorted(codes)

    # -------------------------------------------------------------------------
    # Support account and owner provisioning
    # -------------------------------------------------------------------------

    async def create_tenant_account(
        self,
        *,
        tenant_id: UUID,
        email: str,
        full_name: str,
        phone: str | None = None,
        actor_id: UUID | None = None,
    ) -> tuple[AppUser, TenantMembership]:
        """Support-only account creation. No role is assigned."""
        if await self.repo.get_user_by_email_support(email) is not None:
            raise ConflictError("Пользователь с таким email уже существует")

        user = await self.repo.insert_user(
            email=email.strip(),
            full_name=full_name.strip(),
            phone=phone,
            home_tenant_id=tenant_id,
            is_developer=False,
            is_administrator=False,
            status="invited",
        )
        membership = await self.repo.insert_membership(
            tenant_id=tenant_id,
            user_id=user.id,
            full_name=full_name.strip(),
            phone=phone,
            status="pending",
            created_by=actor_id,
            updated_by=actor_id,
        )
        logger.info(
            "tenant_membership_created",
            tenant_id=str(tenant_id),
            membership_id=str(membership.id),
            user_id=str(user.id),
        )
        return user, membership

    async def provision_owner(
        self,
        *,
        tenant_id: UUID,
        email: str,
        full_name: str,
        actor_id: UUID | None = None,
    ) -> tuple[AppUser, TenantMembership, TenantOwnership, Role]:
        """Atomically create the account, active membership, ownership and
        protected owner assignment. The request transaction is the atomic
        boundary."""
        if not await self.repo.lock_tenant_for_owner_provisioning(tenant_id):
            raise NotFoundError("Tenant not found")
        if await self.repo.count_active_owners(tenant_id) > 0:
            raise ConflictError(
                "Tenant already has an active owner",
                details={"workflow": "Use the protected ownership-transfer workflow"},
            )
        if await self.repo.get_user_by_email_support(email) is not None:
            raise ConflictError("Пользователь с таким email уже существует")

        now = utc_now()
        user = await self.repo.insert_user(
            email=email.strip(),
            full_name=full_name.strip(),
            home_tenant_id=tenant_id,
            is_developer=False,
            is_administrator=False,
            status="active",
            activated_at=now,
        )
        membership = await self.repo.insert_membership(
            tenant_id=tenant_id,
            user_id=user.id,
            full_name=full_name.strip(),
            status="active",
            activated_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )
        ownership = await self.repo.insert_ownership(
            tenant_id=tenant_id,
            membership_id=membership.id,
            is_active=True,
            granted_at=now,
            created_by=actor_id,
            updated_by=actor_id,
        )
        role = await self._ensure_tenant_owner_role(tenant_id, actor_id)
        await self.repo.insert_assignment(
            user_id=user.id,
            tenant_id=tenant_id,
            branch_id=None,
            role_id=role.id,
            password_required=False,
        )
        logger.info(
            "owner_provisioned",
            tenant_id=str(tenant_id),
            membership_id=str(membership.id),
            user_id=str(user.id),
        )
        return user, membership, ownership, role

    async def _ensure_tenant_owner_role(
        self,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> Role:
        template = await self.repo.get_template_by_slug(OWNER_TEMPLATE_SLUG)
        if template is None:
            raise NotFoundError("Шаблон роли «Владелец» не найден")
        codes = await self.repo.get_template_permissions(template.id)

        role = await self.repo.get_role_by_name(OWNER_ROLE_NAME, tenant_id=tenant_id)
        if role is None:
            role = await self.repo.insert_role(
                tenant_id=tenant_id,
                name=OWNER_ROLE_NAME,
                description=template.description,
                level=OWNER_ROLE_LEVEL,
                is_system=False,
                is_protected=True,
                protected_kind="tenant_owner",
                version=1,
                created_by=actor_id,
                updated_by=actor_id,
            )
        elif not role.is_protected or role.protected_kind != "tenant_owner":
            raise ConflictError(
                "Reserved owner role name is already in use",
                details={"role_id": str(role.id)},
            )
        await self.repo.set_role_permissions(role.id, codes)
        return role

    # -------------------------------------------------------------------------
    # Tenant role builder
    # -------------------------------------------------------------------------

    async def create_role(
        self,
        *,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
        tenant_id: UUID,
        name: str,
        description: str | None,
        permission_codes: list[str],
    ) -> tuple[Role, list[str]]:
        self._assert_role_name_available(name)
        codes = await self._validated_role_permissions(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
            requested=permission_codes,
        )
        if await self.repo.get_role_by_name(name, tenant_id=tenant_id) is not None:
            raise ConflictError("A role with this name already exists")

        role = await self.repo.insert_role(
            tenant_id=tenant_id,
            name=name.strip(),
            description=description,
            level=CUSTOM_ROLE_LEGACY_LEVEL,
            is_system=False,
            is_protected=False,
            protected_kind=None,
            version=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
        await self.repo.set_role_permissions(role.id, codes)
        return role, codes

    async def update_role(
        self,
        *,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_is_developer: bool,
        actor_is_administrator: bool,
        tenant_id: UUID,
        role_id: UUID,
        expected_version: int,
        name: str | None,
        description: str | None,
        permission_codes: list[str] | None,
    ) -> tuple[Role, list[str]]:
        role = await self.repo.get_role_for_update(role_id)
        if role is None or role.tenant_id != tenant_id:
            raise NotFoundError("Role not found")
        if role.version != expected_version:
            raise ConflictError(
                "Role version is stale",
                details={
                    "expected_version": expected_version,
                    "current_version": role.version,
                },
            )
        if role.is_system or role.is_protected:
            raise PermissionDeniedError("Protected roles cannot be modified")
        if await self.repo.user_has_active_role(
            tenant_id=tenant_id,
            user_id=actor_id,
            role_id=role.id,
        ):
            raise PermissionDeniedError("You cannot change your own active role")

        current_codes = await self.repo.get_role_permissions(role.id)
        catalog = await self._delegation_catalog(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        allowed = {permission.code for permission in catalog}
        if set(current_codes) - allowed:
            raise PermissionDeniedError(
                "Role contains capabilities outside the delegation envelope"
            )
        requested_codes = current_codes if permission_codes is None else permission_codes
        codes = await self._validated_role_permissions(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
            requested=requested_codes,
        )

        fields: dict[str, object] = {}
        if name is not None and name != role.name:
            self._assert_role_name_available(name)
            clash = await self.repo.get_role_by_name(name, tenant_id=tenant_id)
            if clash is not None and clash.id != role.id:
                raise ConflictError("A role with this name already exists")
            fields["name"] = name.strip()
        if description is not None and description != role.description:
            fields["description"] = description

        permissions_changed = codes != current_codes
        if fields or permissions_changed:
            fields["version"] = role.version + 1
            fields["updated_by"] = actor_id
            role = await self.repo.update_role(role, **fields)

        affected_user_ids = (
            await self.repo.active_user_ids_for_role(role.id, tenant_id=tenant_id)
            if permissions_changed
            else []
        )
        if permissions_changed:
            await self.repo.set_role_permissions(role.id, codes)
        if affected_user_ids:
            await self.invalidate_users_perms(affected_user_ids, tenant_id)
        return role, codes

    @staticmethod
    def _assert_role_name_available(name: str) -> None:
        if name.strip().casefold() in RESERVED_ROLE_NAMES:
            raise PermissionDeniedError("Protected role names are reserved")

    # -------------------------------------------------------------------------
    # Tenant directory and account lifecycle
    # -------------------------------------------------------------------------

    async def list_users(
        self,
        tenant_id: UUID,
        *,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[tuple[DirectoryUser, list[UserAssignment]]], int]:
        total = await self.repo.count_users_for_tenant(tenant_id)
        users = await self.repo.list_users_for_tenant(
            tenant_id,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        by_user: dict[UUID, list[UserAssignment]] = {}
        assignments = await self.repo.assignments_for_users(
            [user.id for user in users],
            tenant_id=tenant_id,
        )
        for assignment in assignments:
            by_user.setdefault(assignment.user_id, []).append(assignment)
        return [(user, by_user.get(user.id, [])) for user in users], total

    async def search_users(
        self,
        tenant_id: UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        role_id: UUID | None = None,
        branch_id: UUID | None = None,
        visible_branch_ids: set[UUID] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[tuple[DirectoryUser, list[UserAssignment]]], int]:
        users, total = await self.repo.search_users_for_tenant(
            tenant_id,
            q=q,
            status=status,
            role_id=role_id,
            branch_id=branch_id,
            visible_branch_ids=visible_branch_ids,
            page=page,
            page_size=page_size,
        )
        assignments = await self.repo.assignments_for_users(
            [user.id for user in users],
            tenant_id=tenant_id,
        )
        if visible_branch_ids is not None:
            assignments = [
                assignment
                for assignment in assignments
                if assignment.branch_id is None or assignment.branch_id in visible_branch_ids
            ]
        by_user: dict[UUID, list[UserAssignment]] = {}
        for assignment in assignments:
            by_user.setdefault(assignment.user_id, []).append(assignment)
        return [(user, by_user.get(user.id, [])) for user in users], total

    async def update_user_profile(
        self,
        *,
        tenant_id: UUID,
        target_user_id: UUID,
        fields: dict[str, object],
    ) -> DirectoryUser:
        user = await self.repo.get_user(target_user_id, tenant_id=tenant_id)
        if user is None:
            raise NotFoundError("User not found in this tenant")
        full_name = str(fields.get("full_name", user.full_name))
        phone_value = fields.get("phone", user.phone)
        if not await self.repo.update_membership_profile(
            tenant_id=tenant_id,
            user_id=target_user_id,
            full_name=full_name,
            phone=None if phone_value is None else str(phone_value),
        ):
            raise NotFoundError("User not found in this tenant")
        updated = await self.repo.get_user(target_user_id, tenant_id=tenant_id)
        if updated is None:
            raise NotFoundError("User not found in this tenant")
        return updated

    async def activate_membership(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> None:
        if actor_id == target_user_id:
            raise BusinessRuleError("You cannot activate your own membership")
        if not await self.repo.set_membership_status(
            tenant_id=tenant_id,
            user_id=target_user_id,
            status="active",
            changed_at=utc_now(),
        ):
            raise NotFoundError("Membership not found")
        await self.invalidate_user_perms(target_user_id, tenant_id)

    async def block_user(
        self,
        *,
        actor_id: UUID,
        actor_is_developer: bool,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> None:
        await self._change_membership_status(
            actor_id=actor_id,
            actor_is_developer=actor_is_developer,
            tenant_id=tenant_id,
            target_user_id=target_user_id,
            target_status="suspended",
        )

    async def revoke_user_sessions(
        self,
        *,
        actor_id: UUID,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> int:
        result = await self.repo.revoke_user_sessions(
            tenant_id=tenant_id,
            target_user_id=target_user_id,
        )
        if result.result == "self":
            raise BusinessRuleError("Use your security page to end your own sessions")
        if result.result == "not_found":
            raise NotFoundError("Membership not found")
        if result.result == "protected":
            raise PermissionDeniedError("Protected account sessions cannot be ended here")
        if result.result != "revoked":
            raise RuntimeError("Unexpected administrative session revocation result")
        logger.info(
            "tenant_user_sessions_revoked",
            actor_user_id=str(actor_id),
            target_user_id=str(target_user_id),
            tenant_id=str(tenant_id),
            revoked_count=result.revoked_count,
        )
        return result.revoked_count

    async def soft_delete_user(
        self,
        *,
        actor_id: UUID,
        actor_is_developer: bool,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> None:
        await self._change_membership_status(
            actor_id=actor_id,
            actor_is_developer=actor_is_developer,
            tenant_id=tenant_id,
            target_user_id=target_user_id,
            target_status="offboarded",
        )

    async def _change_membership_status(
        self,
        *,
        actor_id: UUID,
        actor_is_developer: bool,
        tenant_id: UUID,
        target_user_id: UUID,
        target_status: str,
    ) -> None:
        if actor_id == target_user_id:
            raise BusinessRuleError("You cannot change your own membership status")
        membership = await self.repo.get_membership_for_user(
            tenant_id=tenant_id,
            user_id=target_user_id,
        )
        if membership is None:
            raise NotFoundError("Membership not found")

        ownership = await self.repo.get_active_ownership(
            tenant_id=tenant_id,
            membership_id=membership.id,
        )
        if ownership is not None:
            if await self.repo.count_active_owners(tenant_id) <= 1:
                raise BusinessRuleError("The last active owner cannot be changed")
            if not actor_is_developer:
                raise PermissionDeniedError("Owner lifecycle requires a protected support workflow")

        now = utc_now()
        if not await self.repo.set_membership_status(
            tenant_id=tenant_id,
            user_id=target_user_id,
            status=target_status,
            changed_at=now,
        ):
            raise NotFoundError("Membership not found")
        if ownership is not None:
            await self.repo.deactivate_ownership(
                tenant_id=tenant_id,
                membership_id=membership.id,
                actor_id=actor_id,
                revoked_at=now,
            )
        await self.invalidate_user_perms(target_user_id, tenant_id)

    # -------------------------------------------------------------------------
    # Existing membership assignment
    # -------------------------------------------------------------------------

    @staticmethod
    def _capability_allows_target_scope(
        *,
        permission_code: str,
        branch_id: UUID | None,
        actor_permissions: set[str],
        actor_permission_scopes: Mapping[str, frozenset[UUID] | None],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> bool:
        if actor_is_developer or actor_is_administrator:
            return True
        if permission_code not in actor_permissions:
            return False
        scope = actor_permission_scopes.get(permission_code, frozenset())
        if branch_id is None:
            return scope is None
        return scope is None or branch_id in scope

    def _assert_assignment_scope(
        self,
        *,
        branch_id: UUID | None,
        actor_permissions: set[str],
        actor_permission_scopes: Mapping[str, frozenset[UUID] | None],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> None:
        if not self._capability_allows_target_scope(
            permission_code="roles.assign",
            branch_id=branch_id,
            actor_permissions=actor_permissions,
            actor_permission_scopes=actor_permission_scopes,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        ):
            raise PermissionDeniedError("Assignment is outside your authorized branch scope")

    def _assert_role_delegation_at_scope(
        self,
        *,
        role_codes: list[str],
        branch_id: UUID | None,
        actor_permissions: set[str],
        actor_permission_scopes: Mapping[str, frozenset[UUID] | None],
        actor_is_developer: bool,
        actor_is_administrator: bool,
    ) -> None:
        unavailable = sorted(
            code
            for code in role_codes
            if not self._capability_allows_target_scope(
                permission_code=code,
                branch_id=branch_id,
                actor_permissions=actor_permissions,
                actor_permission_scopes=actor_permission_scopes,
                actor_is_developer=actor_is_developer,
                actor_is_administrator=actor_is_administrator,
            )
        )
        if unavailable:
            raise PermissionDeniedError(
                "Role capabilities are outside your target assignment scope",
                details={"permissions": unavailable},
            )

    async def invite_user(
        self,
        *,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_permission_scopes: Mapping[str, frozenset[UUID] | None],
        actor_is_developer: bool,
        actor_is_administrator: bool,
        tenant_id: UUID,
        email: str,
        full_name: str,
        role_id: UUID,
        branch_id: UUID | None,
        password_required: bool,
    ) -> tuple[DirectoryUser, UserAssignment, bool]:
        """Compatibility route: resolve only an existing tenant membership.

        The full name is intentionally not used. A tenant actor cannot create a
        global account or discover an account outside this tenant.
        """
        del full_name
        membership = await self.repo.find_membership_by_email(
            tenant_id=tenant_id,
            email=email,
        )
        if membership is None:
            raise PermissionDeniedError(
                "Tenant owners cannot create accounts; support must create the membership"
            )
        assignment = await self.assign_role(
            actor_id=actor_id,
            actor_permissions=actor_permissions,
            actor_permission_scopes=actor_permission_scopes,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
            tenant_id=tenant_id,
            target_user_id=membership.user_id,
            role_id=role_id,
            branch_id=branch_id,
            password_required=password_required,
        )
        user = await self.repo.get_user(membership.user_id, tenant_id=tenant_id)
        if user is None:
            raise NotFoundError("Membership not found")
        return user, assignment, False

    async def assign_role(
        self,
        *,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_permission_scopes: Mapping[str, frozenset[UUID] | None],
        actor_is_developer: bool,
        actor_is_administrator: bool,
        tenant_id: UUID,
        target_user_id: UUID,
        role_id: UUID,
        branch_id: UUID | None,
        password_required: bool,
    ) -> UserAssignment:
        if actor_id == target_user_id:
            raise PermissionDeniedError("You cannot assign privileges to yourself")

        role = await self.repo.get_role(role_id)
        if role is None or not role.is_active or role.tenant_id != tenant_id or role.is_system:
            raise NotFoundError("Role not found")
        if role.is_protected:
            raise PermissionDeniedError("Protected roles cannot be assigned")

        role_codes = await self.repo.get_role_permissions(role.id)
        await self._validated_role_permissions(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
            requested=role_codes,
        )
        membership = await self.repo.get_membership_for_user(
            tenant_id=tenant_id,
            user_id=target_user_id,
        )
        if membership is None:
            raise NotFoundError("Tenant membership not found")
        if membership.status not in {"pending", "active"}:
            raise BusinessRuleError(
                "Roles can only be prepared for pending or active memberships",
                details={"membership_status": membership.status},
            )

        self._assert_assignment_scope(
            branch_id=branch_id,
            actor_permissions=actor_permissions,
            actor_permission_scopes=actor_permission_scopes,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        self._assert_role_delegation_at_scope(
            role_codes=role_codes,
            branch_id=branch_id,
            actor_permissions=actor_permissions,
            actor_permission_scopes=actor_permission_scopes,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        existing = await self.repo.list_assignments_for_user(
            target_user_id,
            tenant_id=tenant_id,
        )
        for assignment in existing:
            if assignment.branch_id != branch_id:
                continue
            if assignment.is_active:
                raise ConflictError("User already has an active assignment for this branch")
            reactivated = await self.repo.reactivate_assignment(
                assignment.id,
                tenant_id=tenant_id,
                role_id=role_id,
                password_required=password_required,
            )
            if reactivated is None:
                raise NotFoundError("Assignment not found")
            await self.invalidate_user_perms(target_user_id, tenant_id)
            return reactivated

        assignment = await self.repo.insert_assignment(
            user_id=target_user_id,
            tenant_id=tenant_id,
            branch_id=branch_id,
            role_id=role_id,
            password_required=password_required,
        )
        await self.invalidate_user_perms(target_user_id, tenant_id)
        return assignment

    async def revoke_assignment(
        self,
        *,
        actor_id: UUID,
        actor_permissions: set[str],
        actor_permission_scopes: Mapping[str, frozenset[UUID] | None],
        actor_is_developer: bool,
        actor_is_administrator: bool,
        tenant_id: UUID,
        target_user_id: UUID,
        assignment_id: UUID,
    ) -> None:
        if actor_id == target_user_id:
            raise PermissionDeniedError("You cannot revoke your own privileges")
        assignment = await self.repo.get_assignment(assignment_id)
        if (
            assignment is None
            or assignment.user_id != target_user_id
            or assignment.tenant_id != tenant_id
        ):
            raise NotFoundError("Assignment not found")
        role = await self.repo.get_role(assignment.role_id)
        if role is None:
            raise NotFoundError("Role not found")
        if role.is_protected:
            raise PermissionDeniedError("Protected role assignments cannot be revoked")
        await self._validated_role_permissions(
            actor_id=actor_id,
            tenant_id=tenant_id,
            actor_permissions=actor_permissions,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
            requested=await self.repo.get_role_permissions(role.id),
        )
        self._assert_assignment_scope(
            branch_id=assignment.branch_id,
            actor_permissions=actor_permissions,
            actor_permission_scopes=actor_permission_scopes,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        self._assert_role_delegation_at_scope(
            role_codes=await self.repo.get_role_permissions(role.id),
            branch_id=assignment.branch_id,
            actor_permissions=actor_permissions,
            actor_permission_scopes=actor_permission_scopes,
            actor_is_developer=actor_is_developer,
            actor_is_administrator=actor_is_administrator,
        )
        await self.repo.deactivate_assignment(assignment_id, tenant_id=tenant_id)
        await self.invalidate_user_perms(target_user_id, tenant_id)

    # -------------------------------------------------------------------------
    # Effective permissions
    # -------------------------------------------------------------------------

    async def get_effective_permissions(self, user_id: UUID, tenant_id: UUID) -> set[str]:
        return await self.repo.effective_permissions(user_id, tenant_id)

    async def get_authorization_snapshot(
        self,
        user_id: UUID,
        tenant_id: UUID,
    ) -> AuthorizationSnapshot:
        return await self.repo.authorization_snapshot(user_id, tenant_id)

    async def invalidate_user_perms(self, user_id: UUID, tenant_id: UUID) -> None:
        if self.redis is None:
            return
        await self.redis.delete(perms_cache_key(user_id, tenant_id))
        logger.info(
            "perms_cache_invalidated",
            user_id=str(user_id),
            tenant_id=str(tenant_id),
        )

    async def invalidate_users_perms(
        self,
        user_ids: list[UUID],
        tenant_id: UUID,
    ) -> None:
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
        if self.redis is None:
            return
        pattern = f"{PERMS_CACHE_PREFIX}:{user_id}:*"
        async for key in self.redis.scan_iter(match=pattern):
            await self.redis.delete(key)
        logger.info("perms_cache_purged_for_user", user_id=str(user_id))

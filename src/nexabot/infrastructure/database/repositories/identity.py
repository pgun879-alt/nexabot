"""SQLAlchemy implementation of the identity repository port."""

from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from nexabot.domain.common.errors import NotFoundError
from nexabot.domain.common.ids import UserId
from nexabot.domain.identity.entities import Permission, Role, TelegramIdentity, User, UserStatus
from nexabot.infrastructure.database.base import ensure_utc
from nexabot.infrastructure.database.models import (
    PermissionModel,
    RoleModel,
    TelegramIdentityModel,
    UserModel,
    role_permissions,
    user_roles,
)


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: UserId) -> User | None:
        row = await self._session.get(UserModel, str(user_id))
        if row is None:
            return None
        return await self._to_domain(row)

    async def get_by_telegram_identity(self, telegram_user_id: int) -> User | None:
        identity_row = (
            await self._session.execute(
                sa.select(TelegramIdentityModel).where(
                    TelegramIdentityModel.telegram_user_id == telegram_user_id
                )
            )
        ).scalar_one_or_none()
        if identity_row is None:
            return None
        user_row = await self._session.get(UserModel, identity_row.user_id)
        if user_row is None:
            return None
        return await self._to_domain(user_row)

    async def add(self, user: User) -> None:
        self._session.add(UserModel(id=str(user.id), status=user.status.value))
        await self._session.flush()
        await self._sync_roles(user.id, user.roles)
        if user.telegram_identity is not None:
            await self.attach_telegram_identity(user.id, user.telegram_identity)

    async def save(self, user: User) -> None:
        row = await self._session.get(UserModel, str(user.id))
        if row is None:
            raise NotFoundError("User not found.", code="user_not_found")
        row.status = user.status.value
        await self._sync_roles(user.id, user.roles)
        if user.telegram_identity is not None:
            await self.attach_telegram_identity(user.id, user.telegram_identity)

    async def attach_telegram_identity(self, user_id: UserId, identity: TelegramIdentity) -> None:
        existing = (
            await self._session.execute(
                sa.select(TelegramIdentityModel).where(
                    TelegramIdentityModel.user_id == str(user_id)
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            self._session.add(
                TelegramIdentityModel(
                    id=str(uuid4()),
                    user_id=str(user_id),
                    telegram_user_id=identity.telegram_user_id,
                    username=identity.username,
                    first_name=identity.first_name,
                    last_name=identity.last_name,
                )
            )
        else:
            existing.telegram_user_id = identity.telegram_user_id
            existing.username = identity.username
            existing.first_name = identity.first_name
            existing.last_name = identity.last_name
        await self._session.flush()

    async def _to_domain(self, row: UserModel) -> User:
        identity_row = (
            await self._session.execute(
                sa.select(TelegramIdentityModel).where(TelegramIdentityModel.user_id == row.id)
            )
        ).scalar_one_or_none()
        roles = await self._load_roles(row.id)
        return User(
            id=UserId(UUID(row.id)),
            status=UserStatus(row.status),
            roles=roles,
            telegram_identity=(
                TelegramIdentity(
                    telegram_user_id=identity_row.telegram_user_id,
                    username=identity_row.username,
                    first_name=identity_row.first_name,
                    last_name=identity_row.last_name,
                )
                if identity_row is not None
                else None
            ),
            created_at=ensure_utc(row.created_at),
            updated_at=ensure_utc(row.updated_at),
        )

    async def _load_roles(self, user_id: str) -> frozenset[Role]:
        role_rows = (
            await self._session.execute(
                sa.select(RoleModel)
                .join(user_roles, user_roles.c.role_id == RoleModel.id)
                .where(user_roles.c.user_id == user_id)
            )
        ).scalars()

        roles: set[Role] = set()
        for role_row in role_rows:
            permission_rows = (
                await self._session.execute(
                    sa.select(PermissionModel)
                    .join(role_permissions, role_permissions.c.permission_id == PermissionModel.id)
                    .where(role_permissions.c.role_id == role_row.id)
                )
            ).scalars()
            permissions = frozenset(
                Permission(namespace=p.namespace, action=p.action) for p in permission_rows
            )
            roles.add(Role(name=role_row.name, permissions=permissions))
        return frozenset(roles)

    async def _sync_roles(self, user_id: UserId, roles: frozenset[Role]) -> None:
        await self._session.execute(
            sa.delete(user_roles).where(user_roles.c.user_id == str(user_id))
        )
        for role in roles:
            role_id = await self._get_or_create_role(role)
            await self._session.execute(
                sa.insert(user_roles).values(user_id=str(user_id), role_id=role_id)
            )

    async def _get_or_create_role(self, role: Role) -> str:
        row = (
            await self._session.execute(sa.select(RoleModel).where(RoleModel.name == role.name))
        ).scalar_one_or_none()
        if row is None:
            role_id = str(uuid4())
            self._session.add(RoleModel(id=role_id, name=role.name))
            await self._session.flush()
        else:
            role_id = row.id

        assigned_permission_ids = set(
            (
                await self._session.execute(
                    sa.select(role_permissions.c.permission_id).where(
                        role_permissions.c.role_id == role_id
                    )
                )
            ).scalars()
        )
        for permission in role.permissions:
            permission_id = await self._get_or_create_permission(permission)
            if permission_id not in assigned_permission_ids:
                await self._session.execute(
                    sa.insert(role_permissions).values(role_id=role_id, permission_id=permission_id)
                )
        return role_id

    async def _get_or_create_permission(self, permission: Permission) -> str:
        row = (
            await self._session.execute(
                sa.select(PermissionModel).where(
                    PermissionModel.namespace == permission.namespace,
                    PermissionModel.action == permission.action,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return str(row.id)
        permission_id = str(uuid4())
        self._session.add(
            PermissionModel(
                id=permission_id, namespace=permission.namespace, action=permission.action
            )
        )
        await self._session.flush()
        return permission_id

"""Safe translation of database access policy failures."""

from sqlalchemy.exc import DBAPIError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, PermissionDeniedError


def translate_guard_error(exc: DBAPIError) -> AurumError | None:
    message = str(exc.orig)
    error: AurumError | None = None
    if "tenant_access_pending_settlement" in message:
        error = BusinessRuleError(
            "Сначала завершите проверку оплат и возвратов в разделе «Сверка»."
        )
    elif "tenant_access_quota_exceeded" in message:
        error = BusinessRuleError("Достигнут установленный Aurum лимит. Создание недоступно.")
    elif "tenant_access_policy_conflict" in message:
        error = ConflictError("Ограничения уже изменены. Обновите страницу.")
    elif "tenant_access_restricted" in message:
        error = PermissionDeniedError("Изменения ограничены администратором Aurum.")
    elif "tenant_access_policy_denied" in message:
        error = PermissionDeniedError("Требуется повторное подтверждение полномочий Aurum.")
    elif "tenant_access_policy_missing" in message:
        error = PermissionDeniedError("Политика доступа аптеки недоступна.")
    return error

"""Fixed account permission rules, independent of HTTP and storage."""

MODULE_MDM = "mdm"
MODULE_SALES_ACTUAL = "sales_actual"
MODULE_ORDERING = "ordering"
MODULE_USER_MANAGEMENT = "user_management"
MODULES = (
    MODULE_MDM,
    MODULE_SALES_ACTUAL,
    MODULE_ORDERING,
    MODULE_USER_MANAGEMENT,
)
NONE = "NONE"
VIEW = "VIEW"
EDIT = "EDIT"
LEVEL_RANK = {NONE: 0, VIEW: 1, EDIT: 2}
ACCOUNT_PERMISSION_MIGRATION = "public_permissions_v1"


def role_defaults(role: str) -> dict[str, str]:
    if role == "admin":
        return {module: EDIT for module in MODULES}
    return {
        MODULE_MDM: VIEW,
        MODULE_SALES_ACTUAL: VIEW,
        MODULE_ORDERING: VIEW,
        MODULE_USER_MANAGEMENT: NONE,
    }


def empty_permissions() -> dict[str, str]:
    return {module: NONE for module in MODULES}


def normalize_permissions(value, *, role: str) -> dict[str, str]:
    if value is None:
        return empty_permissions()
    if not isinstance(value, dict) or set(value) - set(MODULES):
        raise ValueError("权限包含未知模块")
    result = empty_permissions()
    for module, level in value.items():
        if level not in LEVEL_RANK:
            raise ValueError("权限等级只能是 NONE、VIEW 或 EDIT")
        if module == MODULE_USER_MANAGEMENT and level == VIEW:
            raise ValueError("User Management 只允许 NONE 或 EDIT")
        result[module] = level
    if role != "admin" and result[MODULE_USER_MANAGEMENT] != NONE:
        raise ValueError("只有 admin 可以拥有 User Management 权限")
    return result


def has_permission(user: dict, module: str, minimum: str) -> bool:
    actual = (user.get("permissions") or {}).get(module, NONE)
    return LEVEL_RANK.get(actual, 0) >= LEVEL_RANK[minimum]

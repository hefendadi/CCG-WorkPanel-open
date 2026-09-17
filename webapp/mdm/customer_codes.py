"""Customer-only code equality and uniqueness checks.

Production uses the existing MySQL utf8mb4_0900_ai_ci collation. Preview and
final workbook checks use its weights too, not a different Python identity.
SQLite's case-insensitive fallback is for isolated unit tests only.
"""

from sqlalchemy import select, text

from .models import Customer


def customer_code_key(session, value):
    if session.get_bind().dialect.name == "mysql":
        return session.scalar(text(
            "SELECT HEX(WEIGHT_STRING(CONVERT(:code USING utf8mb4) "
            "COLLATE utf8mb4_0900_ai_ci))"
        ), {"code": value})
    return value.casefold()


def customer_code_query(session, code, exclude_id=None):
    column = Customer.customer_code
    if session.get_bind().dialect.name == "sqlite":
        column = column.collate("NOCASE")
    query = select(Customer).where(column == code)
    if exclude_id is not None:
        query = query.where(Customer.id != exclude_id)
    return query


def is_customer_code_violation(exc):
    """Recognize only the named Customer code constraint, not unrelated FKs."""
    message = str(exc.orig).lower()
    return (
        "uq_mdm_customer_customer_code" in message
        or "unique constraint failed: mdm_customer.customer_code" in message
    )

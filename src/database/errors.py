from sqlalchemy.exc import SQLAlchemyError


def safe_database_error(error: SQLAlchemyError) -> str:
    """Classify failures using fixed messages; never return driver text or connection data."""
    original = getattr(error, "orig", error)
    code = getattr(original, "sqlstate", None)
    message = str(original).casefold()
    if "network is unreachable" in message or "no route to host" in message:
        return (
            "network unreachable; for Supabase on GitHub Actions use the "
            "Connect > Session pooler URI (port 5432) instead of Direct connection"
        )
    if "tenant or user not found" in message or "tenant/user" in message:
        return "pooler user or host not found; copy the complete Session pooler URI from Connect"
    if code in {"28P01", "28000"} or "password authentication failed" in message:
        return "authentication failed; check the DB password in DATABASE_URL and its URL encoding"
    if any(
        text in message
        for text in [
            "could not translate host name",
            "name or service not known",
            "failed to resolve",
        ]
    ):
        return "hostname could not be resolved; check the copied connection URI and placeholders"
    if "timeout" in message or "connection refused" in message:
        return (
            "connection unavailable; check project status, network restrictions and connection mode"
        )
    if code == "42501":
        return "permission denied; use a database role allowed to create and update the bot tables"
    if code == "3D000":
        return "database not found; copy the database name from the Supabase connection URI"
    if code in {"42P07", "42710", "42P01", "42703"}:
        return "schema mismatch; check existing tables and the Alembic migration history"
    if "ssl" in message or "certificate verify failed" in message:
        return "TLS connection failed; check the database SSL settings and connection URI"
    return "check DATABASE_URL, connection and schema"

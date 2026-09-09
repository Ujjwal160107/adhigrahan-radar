"""Demo-grade role gate: an `X-Role` header + an append-only audit log.

This is explicitly NOT production authentication - there is no session, no
password, no token, and any client can claim any role by setting the
header. It exists to satisfy the one thing the product actually needs from
"access control" at this stage: (a) write actions are visibly gated by
role so the officer workflow reads correctly, and (b) every request is
logged with the role that made it, so "who did what" is answerable.

Real authentication (a login flow, session cookies, server-verified
identity) is explicitly out of scope per CONTRIBUTING.md's "what not to
build" list and is not simulated here as if it existed - the frontend and
docs must say "demo-grade access control" out loud, not imply otherwise.
"""
import logging
from datetime import UTC, datetime

from fastapi import Header, HTTPException

from backend.db import get_conn

logger = logging.getLogger("adhigrahan.auth")

OFFICER = "officer"
DISTRICT_OFFICER = "district_officer"
REVIEWER = "reviewer"
ADMIN = "admin"
ALL_ROLES = (OFFICER, DISTRICT_OFFICER, REVIEWER, ADMIN)
DEFAULT_ROLE = OFFICER

# Reviewer is read-only by design: it exists so a supervisor can see
# everything without being able to change anything.
WRITE_ROLES = (OFFICER, DISTRICT_OFFICER, ADMIN)


def current_role(x_role: str | None = Header(default=None)) -> str:
    """Missing header -> the documented default (officer), never a 500.
    An unrecognised role name is rejected rather than silently accepted."""
    if x_role is None:
        return DEFAULT_ROLE
    if x_role not in ALL_ROLES:
        raise HTTPException(400, {"error": "unknown_role", "known_roles": list(ALL_ROLES)})
    return x_role


def require_write_role(role: str = None) -> None:
    """Call from a route body with the resolved role (FastAPI dependency
    injection order makes a bare Depends()-only guard awkward alongside a
    Pydantic body); raises 403 for a read-only role."""
    if role not in WRITE_ROLES:
        raise HTTPException(403, {"error": "forbidden", "hint": f"role '{role}' is read-only"})


def log_request(role: str, method: str, path: str, status: int) -> None:
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO AuditLog (ts, role, method, path, status) VALUES (?,?,?,?,?)",
            (datetime.now(UTC).isoformat(timespec="seconds"), role, method, path, status),
        )
        conn.commit()
    except Exception:
        # the audit trail must never take the API down; a DB hiccup here
        # is logged, not surfaced to the caller.
        logger.exception("failed to write AuditLog row")

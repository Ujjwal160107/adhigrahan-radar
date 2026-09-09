from fastapi import APIRouter, Header

from backend.auth import ALL_ROLES, WRITE_ROLES, current_role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/session")
def session(x_role: str | None = Header(default=None)):
    """Resolves the caller's role from X-Role (default: officer). This is
    demo-grade access control, not authentication: there is no password,
    no token, and no server-verified identity behind the role - any client
    can claim any role by setting the header. The frontend must render
    this fact, not hide it."""
    role = current_role(x_role)
    return {
        "role": role,
        "can_write": role in WRITE_ROLES,
        "known_roles": list(ALL_ROLES),
        "auth_mode": "demo_role_header",
    }

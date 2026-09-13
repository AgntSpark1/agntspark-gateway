"""Mapping between agntspark_core.auth.Role and the role strings the
console/SDK already speak.

Kept in one place (the API boundary) rather than touched in agntspark_core,
since the naming mismatch (``OPERATOR`` vs ``"developer"``) is a
console/gateway concern, not a core-library one. See the plan's follow-up
notes for a possible future rename in agntspark_core itself.
"""

from __future__ import annotations

from agntspark_core.auth import Role

_ROLE_TO_STR: dict[Role, str] = {
    Role.VIEWER: "viewer",
    Role.OPERATOR: "developer",
    Role.ADMIN: "admin",
}
_STR_TO_ROLE: dict[str, Role] = {v: k for k, v in _ROLE_TO_STR.items()}


def role_to_str(role: Role) -> str:
    return _ROLE_TO_STR[role]


def str_to_role(value: str) -> Role:
    try:
        return _STR_TO_ROLE[value]
    except KeyError as exc:
        raise ValueError(f"Unknown role string: {value!r}") from exc


__all__ = ["role_to_str", "str_to_role"]

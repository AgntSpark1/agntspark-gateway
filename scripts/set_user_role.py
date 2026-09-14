#!/usr/bin/env python3
"""Promote/demote a user's role — the only way to create the first ADMIN.

POST /v1/auth/register always issues the developer role (Role.OPERATOR) —
no public API lets a caller self-grant admin. This maintenance
script is the documented bootstrap path: run it once against the target
database to create your first ADMIN account after registering normally
through the API.

Usage:
    python scripts/set_user_role.py --email founder@agntspark.com --role admin
"""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, ".")

from agntspark_core.auth import Role  # noqa: E402
from sqlalchemy import select  # noqa: E402

from agntspark_gateway.db import AsyncSessionLocal  # noqa: E402
from agntspark_gateway.models.user import User  # noqa: E402
from agntspark_gateway.roles import str_to_role  # noqa: E402


async def set_role(email: str, role: Role) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            print(f"No user found with email {email!r}.", file=sys.stderr)
            raise SystemExit(1)

        old_role = user.role_enum
        user.role = int(role)
        await db.commit()
        print(f"{email}: {old_role.name} -> {role.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", required=True, choices=["viewer", "developer", "admin"])
    args = parser.parse_args()

    role = str_to_role(args.role)
    asyncio.run(set_role(args.email, role))


if __name__ == "__main__":
    main()

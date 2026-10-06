"""Entry point for ``python -m agntspark_gateway`` / the ``agntspark-gateway`` console script."""

from __future__ import annotations

import uvicorn


def main() -> None:
    # RequestContextMiddleware writes the access log, with request ids.
    uvicorn.run("agntspark_gateway.main:app", host="0.0.0.0", port=8080, access_log=False)


if __name__ == "__main__":
    main()

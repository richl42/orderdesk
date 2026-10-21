"""The production start command: `python -m app.serve`.

Listens on every address on the port the host gives in PORT (Render and Railway both set it), and
trusts their proxy's X-Forwarded-* headers, so the app sees HTTPS and the visitor's real address.
One process only: orders are read in background threads of this process, and the database is one
SQLite file on the persistent disk.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
        server_header=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

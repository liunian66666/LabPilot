"""Start the private Zotero MCP sidecar in Docker Compose."""

import os

import uvicorn

from app.zotero_server.server import create_app


def main() -> None:
    token = os.environ.get("ZOTERO_MCP_INTERNAL_TOKEN", "")
    # Refuse to start if there is no strong shared secret.
    app = create_app(token)
    uvicorn.run(app, host="0.0.0.0", port=8765, access_log=False)


if __name__ == "__main__":
    main()

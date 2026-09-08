"""Launch the existing ArcReel backend with loopback-safe process proxy settings."""

import sys
from pathlib import Path

from local_connection import configure_local_connection

configure_local_connection()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ArcReel"))

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server.app:app", host="127.0.0.1", port=1241)

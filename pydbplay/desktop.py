"""Native desktop launcher: boots uvicorn in a background thread, opens a WKWebView window.

Run via `uv run pydbplay-app` in dev, or as the entry point of the py2app `.app` bundle.
"""

import socket
import sys
import threading
import time

import uvicorn
import webview

from pydbplay.cli import _port_available
from pydbplay.config import get_settings

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def main() -> None:
    """Discover a free port, start uvicorn in a daemon thread, then open a WKWebView window."""
    cfg = get_settings()

    if cfg.host not in _LOOPBACK_HOSTS:
        print(
            f"Error: refusing to launch — PYDBPLAY_HOST={cfg.host!r} is not a loopback address. "
            f"Set PYDBPLAY_HOST to one of {sorted(_LOOPBACK_HOSTS)}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 1. Pick a free port
    port: int | None = None
    for candidate in range(cfg.port, cfg.port + 10):
        if _port_available(cfg.host, candidate):
            port = candidate
            break

    if port is None:
        print(
            f"Error: no free port found in range {cfg.port}-{cfg.port + 9}.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. Start uvicorn in a daemon thread
    config = uvicorn.Config(
        "pydbplay.app.main:app",
        host=cfg.host,
        port=port,
        log_level="info",
        lifespan="on",
        reload=False,
    )
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # 3. Wait until reachable (poll every 50 ms, 5-second timeout)
    deadline = time.monotonic() + 5.0
    reachable = False
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((cfg.host, port), timeout=0.1):
                reachable = True
                break
        except OSError:
            time.sleep(0.05)

    if not reachable:
        print(
            f"Error: uvicorn did not become reachable on {cfg.host}:{port} within 5 seconds.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 4. Open WKWebView window (must run on the main thread — Cocoa requirement)
    webview.create_window(
        "pydbplay",
        f"http://{cfg.host}:{port}",
        width=1400,
        height=900,
        resizable=True,
    )
    webview.start()  # blocks until window closes

    # 5. Window closed — ask uvicorn to drain in-flight requests, then exit.
    server.should_exit = True
    thread.join(timeout=10.0)
    if thread.is_alive():
        print(
            "Warning: uvicorn did not shut down within 10s; forcing exit.",
            file=sys.stderr,
        )
    sys.exit(0)


if __name__ == "__main__":
    main()

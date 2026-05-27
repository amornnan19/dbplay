"""CLI entry point: `pydbplay start [--host ...] [--port ...] [--open]`."""

import argparse
import socket
import sys
import webbrowser


def _port_available(host: str, port: int) -> bool:
    """Return True if the given host:port is not yet bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def _cmd_start(args: argparse.Namespace) -> None:
    """Start the uvicorn server bound to localhost."""
    import uvicorn

    host: str = args.host
    port: int = args.port

    if not _port_available(host, port):
        print(
            f"Error: port {port} on {host} is already in use. Choose another port with --port.",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"http://{host}:{port}"
    print(f"Starting pydbplay at {url}")

    if args.open:
        # Open the browser after a brief moment so the server is ready
        import threading

        def _open() -> None:
            import time

            time.sleep(1.0)
            webbrowser.open(url)

        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "pydbplay.app.main:app",
        host=host,
        port=port,
        reload=False,
    )


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate sub-command."""
    parser = argparse.ArgumentParser(
        prog="pydbplay",
        description="Local-first DB query playground.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── start ──────────────────────────────────────────────────────────────
    start_parser = subparsers.add_parser("start", help="Start the web server.")
    start_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind (default: 127.0.0.1). Do NOT use 0.0.0.0 in production.",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        default=7777,
        help="Port to listen on (default: 7777).",
    )
    start_parser.add_argument(
        "--open",
        action="store_true",
        help="Automatically open the browser after startup.",
    )

    args = parser.parse_args()

    if args.command == "start":
        _cmd_start(args)
    else:
        parser.print_help()
        sys.exit(1)

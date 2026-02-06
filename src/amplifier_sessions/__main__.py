"""Amplifier Session Viewer entry point."""

import contextlib
import signal
import socket
import sys
import webbrowser
from pathlib import Path

from .cli import (
    calculate_slug,
    is_session_directory,
    is_sessions_directory,
    parse_args,
    scan_sessions,
    scan_sessions_in_directory,
)
from .server.app import create_app
from .server.state import AppState


def _find_free_port(port: int = 0) -> int:
    """Find a free port to bind to.

    Args:
        port: Preferred port (0 = auto-select)

    Returns:
        Available port number
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        _, port = sock.getsockname()
        return port


def _open_browser(url: str) -> None:
    """Open browser with fallback for different platforms.

    Args:
        url: URL to open
    """
    with contextlib.suppress(Exception):
        webbrowser.open(url)


def main() -> int:
    """Main entry point for amplifier-viewer.

    Context-aware startup: detects the type of directory the user launched
    from and opens the browser to the most relevant page.

    Three launch contexts are supported (checked in order of specificity):

      1. Session directory  (~/.amplifier/projects/<slug>/sessions/<id>/)
         -> Opens /session/<id>  (session detail page)
         -> Also registers sibling sessions so navigation between them works.

      2. Sessions directory (~/.amplifier/projects/<slug>/sessions/)
         -> Opens /projects/<slug>  (project page listing all sessions)

      3. Project directory  (e.g. ~/workspace/my-project/)
         -> Calculates slug, scans for sessions in standard Amplifier location.
         -> If sessions exist: opens /projects/<slug>
         -> If no sessions:    opens /  (root landing page with project browser)

    Returns:
        Exit code
    """
    args = parse_args()

    # Determine the working directory
    work_dir = args.dir if args.dir else "."
    work_path = Path(work_dir).resolve()

    # ---------------------------------------------------------------
    # Context-aware startup URL detection.
    #
    # We detect what kind of directory the user launched from so we can
    # open the browser directly to the most relevant page, instead of
    # always landing on the root page.
    #
    # Three cases, checked in order of specificity:
    #
    #   Case 1 - Session directory:
    #     e.g. ~/.amplifier/projects/<slug>/sessions/<session-id>/
    #     The directory itself contains metadata.json + events.jsonl.
    #     -> Opens browser to /session/<session-id> (session detail page)
    #     -> Registers ALL sibling sessions so navigation still works.
    #
    #   Case 2 - Sessions directory:
    #     e.g. ~/.amplifier/projects/<slug>/sessions/
    #     The directory's children are session folders.
    #     -> Opens browser to /projects/<slug> (project page with session list)
    #
    #   Case 3 - Project working directory:
    #     e.g. ~/workspace/my-project/
    #     Calculate the Amplifier slug and look for sessions.
    #     -> If sessions exist: opens /projects/<slug>
    #     -> If no sessions:    opens / (root landing page)
    #
    # startup_path is the URL path appended to http://127.0.0.1:{port}.
    # Empty string means the root landing page "/".
    # ---------------------------------------------------------------
    startup_path = ""

    if is_session_directory(str(work_path)):
        # ----- Case 1: Inside a specific session folder -----
        # The directory itself contains metadata.json + events.jsonl.
        # We load ALL sibling sessions (not just this one) so the user
        # can navigate between sessions once the app is open.
        session_id = work_path.name
        sessions_parent = work_path.parent  # the "sessions/" directory
        session_paths = scan_sessions_in_directory(str(sessions_parent))
        slug = sessions_parent.parent.name  # the <slug> directory
        startup_path = f"/session/{session_id}"
        print(f"  Opening session {session_id}")

    elif is_sessions_directory(str(work_path)):
        # ----- Case 2: Inside the sessions parent directory -----
        # The directory's children are session folders. Load them all
        # and open the project page that lists every session.
        session_paths = scan_sessions_in_directory(str(work_path))
        slug = work_path.parent.name  # the <slug> directory
        startup_path = f"/projects/{slug}"
        print(f"  Using sessions from {work_path.name}/")

    else:
        # ----- Case 3: Regular project working directory -----
        # Calculate the Amplifier slug and look for sessions in the
        # standard location (~/.amplifier/projects/<slug>/sessions/).
        slug = calculate_slug(str(work_path))
        session_paths = scan_sessions(slug)
        if session_paths:
            # Sessions found -> jump straight to the project page
            startup_path = f"/projects/{slug}"
        # else: no sessions found, startup_path stays "" -> landing page

    # Initialize app state
    app_state = AppState()
    app_state.current_slug = slug

    # Store session paths for on-demand loading.
    # Sessions are NOT parsed at startup -- they load lazily when the
    # user navigates to /session/<id>. This keeps startup fast.
    if session_paths:
        print(f"  Found {len(session_paths)} session(s)")
        app_state.session_paths = {Path(p).name: p for p in session_paths}
        print("  Sessions will load on-demand when selected...")
    else:
        print("  No sessions in current directory")
        print("  Starting with project browser...")

    # Find available port
    port = args.port if args.port > 0 else _find_free_port()

    # Create Flask app
    app = create_app(app_state)

    # Open browser at the context-appropriate page.
    # startup_path was determined above based on directory detection:
    #   "/session/<id>"    -> session detail page    (Case 1)
    #   "/projects/<slug>" -> project session list   (Case 2 & 3 with sessions)
    #   ""                 -> root landing page      (Case 3 without sessions)
    base_url = f"http://127.0.0.1:{port}"
    url = f"{base_url}{startup_path}"
    print(f"\n  Viewer running at {base_url}")
    if startup_path:
        print(f"  Opening {url}")
    print("  Press Ctrl+C to exit\n")
    _open_browser(url)

    # Handle graceful shutdown
    def signal_handler(sig, frame):  # type: ignore
        print("\n  Shutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Run server
    try:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())

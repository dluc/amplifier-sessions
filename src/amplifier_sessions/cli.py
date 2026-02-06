"""CLI argument parsing and folder slug calculation."""

import argparse
import os
from pathlib import Path


def calculate_slug(directory: str) -> str:
    """Calculate folder slug from directory path.

    Matches Amplifier's slug calculation: replace path separators with dashes.

    Args:
        directory: Directory path

    Returns:
        Slug (absolute path with / replaced by -)
    """
    abs_path = os.path.abspath(directory)
    # Amplifier replaces path separators with dashes
    # /Users/me/workspace/made/amplifier-sessions → -Users-me-workspace-made-amplifier-sessions
    slug = abs_path.replace(os.sep, "-")
    return slug


def is_session_directory(path: str) -> bool:
    """Check if path is a single session directory (not the parent sessions dir).

    A session directory directly contains metadata.json and events.jsonl,
    as opposed to a sessions directory whose *children* contain those files.

    Example session directory:
        ~/.amplifier/projects/<slug>/sessions/<session-id>/
        ├── metadata.json
        ├── events.jsonl
        └── transcript.jsonl

    Args:
        path: Directory path to check

    Returns:
        True if the directory itself contains session files (metadata.json + events.jsonl)
    """
    path_obj = Path(path).resolve()

    if not path_obj.exists() or not path_obj.is_dir():
        return False

    # A session directory has metadata.json and events.jsonl directly inside it
    metadata = path_obj / "metadata.json"
    events = path_obj / "events.jsonl"
    return metadata.exists() and events.exists()


def is_sessions_directory(path: str) -> bool:
    """Check if path is a sessions directory.

    Sessions directory structure:
    ~/.amplifier/projects/<slug>/sessions/
    ├── <session-id>/
    │   ├── metadata.json
    │   ├── events.jsonl
    │   └── transcript.jsonl

    Args:
        path: Directory path to check

    Returns:
        True if directory contains session subdirectories
    """
    path_obj = Path(path).resolve()

    # Check if any subdirectory contains metadata.json and events.jsonl
    if not path_obj.exists() or not path_obj.is_dir():
        return False

    try:
        for item in path_obj.iterdir():
            if item.is_dir():
                # Check if this looks like a session directory
                metadata = item / "metadata.json"
                events = item / "events.jsonl"
                if metadata.exists() and events.exists():
                    return True
    except (OSError, PermissionError):
        return False

    return False


def detect_project_from_sessions_dir(sessions_path: str) -> str | None:
    """Detect project directory from a sessions directory.

    Given ~/.amplifier/projects/<slug>/sessions, return the original project path.

    Args:
        sessions_path: Path to sessions directory

    Returns:
        Project directory path or None if not detectable
    """
    path = Path(sessions_path).resolve()

    # Check if this is ~/.amplifier/projects/<slug>/sessions
    if path.name != "sessions":
        return None

    parent = path.parent
    if parent.name != "projects":
        return None

    # We have ~/.amplifier/projects/<slug>
    # The slug is the MD5 hash of the project directory
    # We can't reverse the hash, so just return None
    # (User can pass --dir if they want to use a different project)
    return None


def scan_sessions(slug: str) -> list[str]:
    """Scan for sessions in ~/.amplifier/projects/<slug>/sessions/.

    Args:
        slug: Folder slug

    Returns:
        List of absolute paths to session directories
    """
    home = Path.home()
    sessions_dir = home / ".amplifier" / "projects" / slug / "sessions"

    if not sessions_dir.exists():
        return []

    return sorted([str(d) for d in sessions_dir.iterdir() if d.is_dir()])


def scan_sessions_in_directory(directory: str) -> list[str]:
    """Scan for sessions in a given directory (which should be a sessions folder).

    Args:
        directory: Path to sessions directory

    Returns:
        List of absolute paths to session directories
    """
    sessions_dir = Path(directory).resolve()

    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return []

    return sorted([str(d) for d in sessions_dir.iterdir() if d.is_dir()])


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="Amplifier Session Viewer - Rich session analysis tool",
        prog="amplifier-viewer",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help="Project directory (default: current directory). "
        + "Auto-detects if you cd into ~/.amplifier/projects/<slug>/sessions/",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port to run server on (default: auto-select)",
    )
    return parser.parse_args()

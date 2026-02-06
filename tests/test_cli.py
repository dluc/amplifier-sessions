"""Tests for CLI functionality."""

import tempfile
from pathlib import Path

from amplifier_sessions.cli import calculate_slug, is_sessions_directory


def test_calculate_slug():
    """Test slug calculation is deterministic and matches Amplifier's format."""
    path = "/Users/test/project"
    slug1 = calculate_slug(path)
    slug2 = calculate_slug(path)

    assert slug1 == slug2
    # Slug is path with / replaced by -
    assert slug1 == "-Users-test-project"


def test_calculate_slug_different_paths():
    """Test different paths produce different slugs."""
    slug1 = calculate_slug("/path/one")
    slug2 = calculate_slug("/path/two")

    assert slug1 != slug2


def test_is_sessions_directory_valid():
    """Test detection of valid sessions directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create session structure
        session_dir = Path(tmpdir) / "session-123"
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text("{}")
        (session_dir / "events.jsonl").write_text("")

        assert is_sessions_directory(tmpdir)


def test_is_sessions_directory_empty():
    """Test detection fails for empty directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        assert not is_sessions_directory(tmpdir)


def test_is_sessions_directory_invalid_structure():
    """Test detection fails for incomplete session structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create session with only metadata
        session_dir = Path(tmpdir) / "session-123"
        session_dir.mkdir()
        (session_dir / "metadata.json").write_text("{}")
        # Missing events.jsonl

        assert not is_sessions_directory(tmpdir)


def test_is_sessions_directory_nonexistent():
    """Test detection fails for nonexistent path."""
    assert not is_sessions_directory("/nonexistent/path")


def test_is_sessions_directory_file():
    """Test detection fails for file instead of directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        file_path = Path(tmpdir) / "file.txt"
        file_path.write_text("test")

        assert not is_sessions_directory(str(file_path))

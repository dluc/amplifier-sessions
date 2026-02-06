"""Utility functions for the viewer."""

from datetime import datetime, timezone


def shorten_path(path: str) -> str:
    """Replace user home directory with ~ for display.

    Args:
        path: The file path to shorten

    Returns:
        Path with /Users/username or /home/username replaced with ~
    """
    if path.startswith("/Users/"):
        parts = path.split("/")
        if len(parts) > 2:
            # Replace /Users/username with ~
            return "~" + "/" + "/".join(parts[3:])
    elif path.startswith("/home/"):
        parts = path.split("/")
        if len(parts) > 2:
            # Replace /home/username with ~
            return "~" + "/" + "/".join(parts[3:])
    return path


def humanize_time_diff(dt: datetime) -> str:
    """Convert a datetime to a human-readable relative time.

    Args:
        dt: The datetime to format

    Returns:
        Human-readable string like "3 mins ago" or "2 days ago"
    """
    # Ensure dt is timezone-aware for comparison
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    diff = now - dt

    seconds = int(diff.total_seconds())

    # Handle negative differences (future dates)
    if seconds < 0:
        return "just now"

    # Seconds
    if seconds < 60:
        return "just now" if seconds < 30 else f"{seconds} secs ago"

    # Minutes
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago" if minutes == 1 else f"{minutes} mins ago"

    # Hours
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour ago" if hours == 1 else f"{hours} hours ago"

    # Days
    days = hours // 24
    if days < 7:
        return f"{days} day ago" if days == 1 else f"{days} days ago"

    # Weeks
    weeks = days // 7
    if weeks < 4:
        return f"{weeks} week ago" if weeks == 1 else f"{weeks} weeks ago"

    # Months (approximate)
    months = days // 30
    if months < 12:
        return f"{months} month ago" if months == 1 else f"{months} months ago"

    # Years
    years = days // 365
    return f"{years} year ago" if years == 1 else f"{years} years ago"


def format_datetime_local(dt: datetime) -> str:
    """Format datetime for display.

    Args:
        dt: The datetime to format

    Returns:
        ISO format string (YYYY-MM-DD HH:MM:SS)
    """
    if dt.tzinfo is not None:
        # Convert to local time if timezone-aware
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_bytes(size_bytes: int) -> str:
    """Format bytes as human-readable string.

    Args:
        size_bytes: Size in bytes

    Returns:
        Human-readable string like "1.5 MB" or "256 KB"
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"

    size_float = float(size_bytes)
    for unit in ["KB", "MB", "GB"]:
        size_float /= 1024
        if size_float < 1024:
            return f"{size_float:.1f} {unit}"

    return f"{size_float:.1f} TB"

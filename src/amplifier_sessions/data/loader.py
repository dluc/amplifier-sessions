"""Load and parse Amplifier session files."""

import json
from datetime import datetime
from pathlib import Path

from .indexer import build_index
from .types import Event, Project, Session, SessionData, TranscriptTurn

# ---------------------------------------------------------------------------
# Fast helpers for metadata-only loading
#
# PERFORMANCE FIX: The list page (project_detail route) only needs session
# metadata (name, counts, timestamps, file sizes) -- none of which require
# parsing event or transcript JSON.  Previously, the list page called
# load_session() which parsed every line of events.jsonl as JSON and built
# Pydantic Event models + indexes.  For a project with 5GB of events this
# took ~60s and ~10GB RAM.
#
# These helpers avoid JSON parsing entirely:
#   _count_lines()              -- counts newlines via binary chunk reads
#   _read_last_event_timestamp()-- seeks to EOF, parses only the last line
#
# Together they bring list-page load from ~60s down to ~2-5s for 5GB.
# ---------------------------------------------------------------------------


def _count_lines(filepath: Path) -> int:
    """Count newlines in a file using fast binary chunk reading.

    Opens the file in binary mode and counts ``\\n`` bytes in 64KB chunks.
    This avoids:
      - Text-mode decoding overhead (UTF-8 decode per line)
      - Python string object creation per line
      - Any JSON parsing or Pydantic validation

    Performance: ~0.5s per GB on SSD vs ~10s for ``sum(1 for _ in f)``
    in text mode, and orders of magnitude faster than json.loads() per line.

    Note: the count may differ slightly from the parsed event count returned
    by load_session(), which skips empty/malformed lines.  For the list page
    this approximation is acceptable -- exact counts are shown on the detail
    page after full parsing.
    """
    if not filepath.exists():
        return 0
    count = 0
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(1 << 16)  # 64KB -- balances syscall count vs memory
            if not chunk:
                break
            count += chunk.count(b"\n")
    return count


def _read_last_event_timestamp(events_file: Path) -> datetime | None:
    """Read the timestamp from the last event without loading the whole file.

    PERFORMANCE: Seeks to (EOF - 64KB) and reads only that tail chunk, then
    walks backwards through lines to find the last non-empty JSON line and
    parses just its ``ts`` field.  This is effectively O(1) regardless of
    file size -- even for multi-GB files only ~64KB is read.

    64KB is far more than enough for a single JSONL line (most events are
    a few hundred bytes; the largest tool_call results are ~50KB).

    Returns None if the file is missing, empty, or the last line(s) cannot
    be parsed -- callers fall back to metadata timestamp fields.
    """
    if not events_file.exists():
        return None

    file_size = events_file.stat().st_size
    if file_size == 0:
        return None

    with open(events_file, "rb") as f:
        # Only read the tail of the file -- no need to touch earlier bytes.
        read_size = min(file_size, 1 << 16)
        f.seek(file_size - read_size)
        chunk = f.read()

    # Walk backwards to skip trailing empty lines and find the last real event.
    # If the last line is malformed, try the one before it (defensive).
    for line in reversed(chunk.split(b"\n")):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            ts_str = data.get("ts", "")
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (json.JSONDecodeError, ValueError, AttributeError):
            # Malformed trailing line -- try the previous one
            continue

    return None


# ---------------------------------------------------------------------------
# Transcript message preview helpers
#
# PERFORMANCE: The list page shows the first and last message of each session.
# These helpers read only the first line and last ~64KB of transcript.jsonl --
# they NEVER iterate through the entire file.  For a 100MB transcript this
# means reading ~128KB total instead of 100MB.
#
# Transcript lines can have complex content:
#   - String content (user messages, tool results): used directly
#   - Array content (assistant messages): contains "thinking", "text", and
#     "tool_call" blocks.  We extract only "text" blocks and skip thinking/
#     tool_call which are noise for a preview.
# ---------------------------------------------------------------------------


def _extract_transcript_text(data: dict) -> tuple[str, str]:
    """Extract (role, displayable_text) from a raw transcript.jsonl line.

    Handles both string content and array content (assistant messages with
    thinking/text/tool_call blocks).  Returns plain text suitable for a
    truncated preview.
    """
    role = data.get("role", "")
    content = data.get("content", "")

    if isinstance(content, str):
        return role, content

    if isinstance(content, list):
        # Array content -- extract "text" blocks, skip thinking/tool_call.
        # This gives a clean preview of the assistant's actual response.
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        if parts:
            return role, "\n".join(parts)
        # No text blocks -- fall back to a summary of what's there
        types = [item.get("type", "?") for item in content if isinstance(item, dict)]
        return role, f"[{', '.join(types)}]" if types else ""

    return role, str(content) if content else ""


def _read_first_transcript_message(transcript_file: Path) -> tuple[str, str]:
    """Read the first message from transcript.jsonl.

    PERFORMANCE: Opens the file and reads lines until the first non-empty
    one is found, then closes.  Reads at most a few KB regardless of file
    size.

    Returns (role, content) or ("", "") if the file is missing/empty.
    """
    if not transcript_file.exists():
        return "", ""

    try:
        with open(transcript_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                data = json.loads(line)
                return _extract_transcript_text(data)
    except (json.JSONDecodeError, OSError):
        pass

    return "", ""


def _read_last_transcript_message(transcript_file: Path) -> tuple[str, str]:
    """Read the last message from transcript.jsonl.

    PERFORMANCE: Seeks to (EOF - 64KB) and reads only that tail chunk,
    then walks backwards through lines to find the last non-empty JSON
    line.  Effectively O(1) regardless of file size.

    Returns (role, content) or ("", "") if the file is missing/empty.
    """
    if not transcript_file.exists():
        return "", ""

    try:
        file_size = transcript_file.stat().st_size
        if file_size == 0:
            return "", ""

        with open(transcript_file, "rb") as f:
            read_size = min(file_size, 1 << 16)  # last 64KB
            f.seek(file_size - read_size)
            chunk = f.read()

        for raw_line in reversed(chunk.split(b"\n")):
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            data = json.loads(raw_line)
            return _extract_transcript_text(data)
    except (json.JSONDecodeError, OSError):
        pass

    return "", ""


# ---------------------------------------------------------------------------
# Metadata-only loading (for list pages)
#
# These functions return Session objects (not SessionData) because the list
# page never needs events, transcripts, or indexes.  The full load_session()
# path is used only when the user clicks into a specific session detail page.
#
# IMPORTANT: Behavioral differences vs load_session():
#   - event_count / transcript_turns are *line counts*, not parsed object
#     counts.  load_session() may report slightly different numbers because
#     it skips empty lines, counts malformed lines separately, and expands
#     multi-block transcript entries into multiple TranscriptTurn objects.
#     The line count is a close-enough approximation for the list page.
#   - malformed_count is always 0 (detecting malformed lines requires
#     actually parsing JSON, which is what we're avoiding).
#   - updated_at comes from _read_last_event_timestamp() which reads
#     only the tail of the file, matching the same logic in load_session()
#     (last event timestamp) but without loading all events.
# ---------------------------------------------------------------------------


def load_session_metadata(session_path: str) -> Session:
    """Load session metadata WITHOUT parsing events or transcript content.

    This is the fast path used by the project list page.  It gathers every
    field the list.html template needs without touching event/transcript
    content:

        metadata.json   -> name, description, bundle, model, agent info, etc.
        _count_lines()  -> event_count, transcript_turns  (binary newline count)
        stat()          -> events_size, transcript_size    (file sizes in bytes)
        _read_last_event_timestamp() -> updated_at         (last ~64KB only)

    For a session with 1GB events.jsonl this completes in ~0.5s.
    The full load_session() on the same file takes ~12s+ (JSON parse + Pydantic).
    """
    session_dir = Path(session_path)
    session_id = session_dir.name

    # --- metadata.json (always small, <1KB typically) ---
    metadata_file = session_dir / "metadata.json"
    with open(metadata_file) as f:
        metadata = json.load(f)

    # Amplifier stores "created" (not "created_at") in newer sessions;
    # fall back to "created_at" for older formats.
    created_str = metadata.get("created") or metadata.get("created_at") or ""
    try:
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        created_at = datetime.now()

    # --- updated_at: last event timestamp (reads only tail ~64KB of file) ---
    # This mirrors load_session() which uses events[-1].timestamp.
    # Falls back to metadata timestamp fields if events.jsonl is missing/empty.
    events_file = session_dir / "events.jsonl"
    last_event_ts = _read_last_event_timestamp(events_file)

    if last_event_ts:
        updated_at = last_event_ts
    else:
        # No events or parse failure -- fall back to metadata fields
        update_candidates = [
            metadata.get("description_updated_at"),
            metadata.get("name_generated_at"),
            metadata.get("updated_at"),
        ]
        updated_str = next((s for s in update_candidates if s), created_str)
        try:
            updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            updated_at = created_at

    # --- Line counts (fast binary counting -- no JSON parsing) ---
    # See _count_lines() docstring for why this is ~20x faster than text-mode
    # iteration and orders of magnitude faster than json.loads() per line.
    event_count = _count_lines(events_file)
    transcript_file = session_dir / "transcript.jsonl"
    transcript_turns = _count_lines(transcript_file)

    # --- File sizes (single stat() syscall each -- effectively instant) ---
    events_size = events_file.stat().st_size if events_file.exists() else 0
    metadata_size = metadata_file.stat().st_size if metadata_file.exists() else 0
    transcript_size = transcript_file.stat().st_size if transcript_file.exists() else 0

    # --- First/last transcript messages (for list-page preview) ---
    # PERFORMANCE: Reads only the first line and last ~64KB of transcript.jsonl.
    # Never iterates through the file.  For a 100MB transcript this touches
    # ~128KB total.  Adds negligible time (<1ms) to the metadata-only load.
    first_msg_role, first_msg_content = _read_first_transcript_message(transcript_file)
    last_msg_role, last_msg_content = _read_last_transcript_message(transcript_file)

    # --- Session identity and agent fields (all from metadata.json) ---
    # Path structure: ~/.amplifier/projects/<slug>/sessions/<session-id>
    slug = session_dir.parent.parent.name
    title = metadata.get("title", session_id)
    parent_id = metadata.get("parent_id", "")
    agent_name = metadata.get("agent_name", "")
    agent_description = ""
    agent_instruction = ""

    if agent_name:
        # Agent sessions store extra info in agent_overlay
        agent_overlay = metadata.get("agent_overlay", {})
        agent_description = agent_overlay.get("description", "")
        agent_instruction = agent_overlay.get("instruction", "")

    return Session(
        id=session_id,
        slug=slug,
        path=str(session_dir),
        created_at=created_at,
        updated_at=updated_at,
        title=title,
        event_count=event_count,
        malformed_count=0,  # Not available without full JSON parse
        transcript_turns=transcript_turns,
        bundle=metadata.get("bundle", ""),
        model=metadata.get("model", ""),
        working_dir=metadata.get("working_dir", ""),
        name=metadata.get("name", ""),
        description=metadata.get("description", ""),
        parent_id=parent_id,
        agent_name=agent_name,
        agent_description=agent_description,
        agent_instruction=agent_instruction,
        events_size=events_size,
        metadata_size=metadata_size,
        transcript_size=transcript_size,
        first_message_role=first_msg_role,
        first_message_content=first_msg_content,
        last_message_role=last_msg_role,
        last_message_content=last_msg_content,
    )


def load_sessions_metadata_for_slug(slug: str) -> list[Session]:
    """Load metadata-only Session objects for every session in a project.

    This is the fast alternative to load_sessions_for_slug().  It returns
    Session objects (not SessionData) because the list page never needs
    events, transcripts, or indexes.

    No caching is done here -- metadata loading is fast enough (~0.5s/GB)
    that re-reading on each page visit is acceptable and avoids stale-cache
    bugs.  Full session data is cached by the session_detail route when a
    user clicks into a specific session.
    """
    projects_dir = Path.home() / ".amplifier" / "projects" / slug
    sessions_dir = projects_dir / "sessions"

    if not sessions_dir.exists():
        return []

    sessions: list[Session] = []
    for session_path in sorted(sessions_dir.iterdir()):
        if not session_path.is_dir():
            continue
        try:
            session = load_session_metadata(str(session_path))
            sessions.append(session)
        except Exception:
            # Skip sessions with missing/corrupt metadata.json
            continue

    return sessions


# ---------------------------------------------------------------------------
# Full loading (for session detail pages only)
#
# These functions parse events.jsonl and transcript.jsonl into Python objects,
# build filtering indexes, and return SessionData.  They are EXPENSIVE:
#   - ~12s per GB of events.jsonl (JSON parse + Pydantic Event creation)
#   - Memory: ~2x the file size (raw dicts + Pydantic model copies)
#
# Only the session_detail route calls load_session(), and it caches the
# result in AppState so repeated visits don't re-parse.
#
# DO NOT call load_sessions_for_slug() from the list page -- use
# load_sessions_metadata_for_slug() instead (see above).
# ---------------------------------------------------------------------------


def load_sessions_for_slug(slug: str) -> list[SessionData]:
    """Load all sessions for a specific project slug from disk.

    WARNING: This parses ALL events and transcripts for ALL sessions.
    For the list page, use load_sessions_metadata_for_slug() instead.

    Args:
        slug: Project slug to load sessions for

    Returns:
        List of SessionData objects for that slug
    """
    projects_dir = Path.home() / ".amplifier" / "projects" / slug
    sessions_dir = projects_dir / "sessions"

    if not sessions_dir.exists():
        return []

    sessions = []
    for session_path in sorted(sessions_dir.iterdir()):
        if not session_path.is_dir():
            continue
        try:
            session_data = load_session(str(session_path))
            sessions.append(session_data)
        except Exception:
            # Skip sessions that fail to load
            continue

    return sessions


def load_projects() -> list[Project]:
    """Load all projects from ~/.amplifier/projects/ (metadata only, no events).

    Scans ~/.amplifier/projects/ and extracts project metadata from the oldest
    root session in each project. Does NOT load events or transcripts.

    Returns:
        List of Project objects sorted by most recently updated first
    """
    projects_dir = Path.home() / ".amplifier" / "projects"

    if not projects_dir.exists():
        return []

    projects = []

    # Iterate through all project folders
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue

        slug = project_dir.name
        sessions_dir = project_dir / "sessions"

        if not sessions_dir.exists():
            continue

        # Load METADATA ONLY for all sessions in this project
        sessions_metadata = []
        for session_path in sessions_dir.iterdir():
            if not session_path.is_dir():
                continue
            try:
                metadata_file = session_path / "metadata.json"
                if not metadata_file.exists():
                    continue
                with open(metadata_file) as f:
                    metadata = json.load(f)
                sessions_metadata.append((session_path.name, metadata))
            except Exception:
                # Skip sessions with bad metadata
                continue

        if not sessions_metadata:
            continue

        # Find root sessions (those with no parent_id)
        root_metadata = [(sid, meta) for sid, meta in sessions_metadata if not meta.get("parent_id")]

        if not root_metadata:
            continue

        # Find the oldest root session by created timestamp
        def get_created(item):
            sid, meta = item
            created_str = meta.get("created") or meta.get("created_at") or ""
            try:
                return datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return datetime.now()

        oldest_root_sid, oldest_root_meta = min(root_metadata, key=get_created)

        # Find the most recently updated session overall
        def get_updated(item):
            sid, meta = item
            # Check: description_updated_at, name_generated_at, updated_at, created
            update_candidates = [
                meta.get("description_updated_at"),
                meta.get("name_generated_at"),
                meta.get("updated_at"),
                meta.get("created"),
                meta.get("created_at"),
            ]
            updated_str = next((s for s in update_candidates if s), "")
            try:
                return datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return datetime.now()

        latest_sid, latest_meta = max(sessions_metadata, key=get_updated)

        # Extract name and description from oldest root
        name = oldest_root_meta.get("name", "") or oldest_root_meta.get("title", "")
        description = oldest_root_meta.get("description", "")
        working_dir = oldest_root_meta.get("working_dir", "")

        # Build chronologically-sorted list of root session names.
        # Uses the already-loaded root_metadata (no extra I/O) -- just
        # sort by created timestamp and extract the name field.
        sorted_roots = sorted(root_metadata, key=get_created)
        root_session_names = [meta.get("name", "") or meta.get("title", "") or sid for sid, meta in sorted_roots]

        # Create Project object
        project = Project(
            slug=slug,
            path=str(project_dir),
            name=name,
            description=description,
            created_at=get_created((oldest_root_sid, oldest_root_meta)),
            updated_at=get_updated((latest_sid, latest_meta)),
            session_count=len(sessions_metadata),
            root_session_names=root_session_names,
            working_dir=working_dir,
        )

        projects.append(project)

    # Sort by most recently updated first
    projects.sort(key=lambda p: p.updated_at, reverse=True)

    return projects


def load_session(session_path: str) -> SessionData:
    """Load a complete session from a directory (EXPENSIVE).

    Parses every line of events.jsonl and transcript.jsonl into Pydantic
    models, then builds in-memory indexes for filtering.  Use only for the
    session detail page where the full data is needed.

    Cost: ~12s per GB of events.jsonl, memory ~2x file size.
    For list pages, use load_session_metadata() instead.

    Args:
        session_path: Path to session directory containing metadata.json,
            events.jsonl, transcript.jsonl

    Returns:
        SessionData with loaded and indexed events
    """
    session_dir = Path(session_path)
    session_id = session_dir.name

    # Load metadata (small file, always fast)
    metadata_file = session_dir / "metadata.json"
    with open(metadata_file) as f:
        metadata = json.load(f)

    # Parse timestamps - Amplifier uses "created" not "created_at"
    created_str = metadata.get("created") or metadata.get("created_at") or ""

    try:
        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        created_at = datetime.now()

    # Find the latest update time from available fields
    # Check: description_updated_at, name_generated_at, updated_at
    update_candidates = [
        metadata.get("description_updated_at"),
        metadata.get("name_generated_at"),
        metadata.get("updated_at"),
    ]
    updated_str = next((s for s in update_candidates if s), created_str)

    try:
        updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        updated_at = created_at

    # Load events
    events_file = session_dir / "events.jsonl"
    events, malformed_count = _load_events(events_file)

    # Load transcript
    transcript_file = session_dir / "transcript.jsonl"
    transcript = _load_transcript(transcript_file)

    # Calculate true last update: the timestamp of the final event (when session actually ended)
    # Fallback to metadata fields if no events, then to created_at
    if events:
        # Last event's timestamp is the true "last update"
        updated_at = events[-1].timestamp
    else:
        # No events: check metadata fields, fallback to created_at
        update_candidates = [
            metadata.get("description_updated_at"),
            metadata.get("name_generated_at"),
            metadata.get("updated_at"),
        ]
        updated_str = next((s for s in update_candidates if s), created_str)
        try:
            updated_at = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            updated_at = created_at

    # Extract folder slug from parent structure
    # Path: ~/.amplifier/projects/<slug>/sessions/<session-id>
    slug = session_dir.parent.parent.name

    # Create Session object
    # Use full ID as default title to distinguish parent/child sessions
    default_title = session_id
    title = metadata.get("title", default_title)

    # Get file sizes
    events_size = (session_dir / "events.jsonl").stat().st_size if (session_dir / "events.jsonl").exists() else 0
    metadata_size = (session_dir / "metadata.json").stat().st_size if (session_dir / "metadata.json").exists() else 0
    transcript_size = (
        (session_dir / "transcript.jsonl").stat().st_size if (session_dir / "transcript.jsonl").exists() else 0
    )

    # Extract agent session fields if present
    parent_id = metadata.get("parent_id", "")
    agent_name = metadata.get("agent_name", "")
    agent_description = ""
    agent_instruction = ""

    if agent_name:
        # This is an agent session - extract from agent_overlay
        agent_overlay = metadata.get("agent_overlay", {})
        agent_description = agent_overlay.get("description", "")
        agent_instruction = agent_overlay.get("instruction", "")

    session = Session(
        id=session_id,
        slug=slug,
        path=str(session_dir),
        created_at=created_at,
        updated_at=updated_at,
        title=title,
        event_count=len(events),
        malformed_count=malformed_count,
        transcript_turns=len(transcript),
        bundle=metadata.get("bundle", ""),
        model=metadata.get("model", ""),
        working_dir=metadata.get("working_dir", ""),
        name=metadata.get("name", ""),
        description=metadata.get("description", ""),
        parent_id=parent_id,
        agent_name=agent_name,
        agent_description=agent_description,
        agent_instruction=agent_instruction,
        events_size=events_size,
        metadata_size=metadata_size,
        transcript_size=transcript_size,
    )

    # Build index for fast filtering
    index = build_index(events)

    return SessionData(session=session, events=events, index=index, transcript=transcript)


def _load_events(events_file: Path) -> tuple[list[Event], int]:
    """Load and parse events.jsonl (the most expensive operation in the app).

    Every line is decoded as JSON, then transformed into a Pydantic Event
    model that stores curated fields *plus* the full raw dict (metadata=data).
    For a 1GB file this means:
      - Millions of json.loads() calls (CPU-bound)
      - Millions of Pydantic model instantiations (validation overhead)
      - ~2x memory: the raw dict + the model's copy of each field

    This is intentional for the detail page (we need full event data for
    filtering and display), but must NEVER be called from the list page.
    The list page uses _count_lines() instead (~20x faster, zero memory).

    Args:
        events_file: Path to events.jsonl

    Returns:
        Tuple of (list of Event objects, count of malformed lines)
    """
    events = []
    malformed_count = 0

    with open(events_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                event = _parse_event(data)
                events.append(event)
            except (json.JSONDecodeError, ValueError, KeyError):
                malformed_count += 1

    return events, malformed_count


def _parse_event(data: dict) -> Event:
    """Parse a single event from JSON.

    Args:
        data: Raw JSON data from events.jsonl line

    Returns:
        Event object with curated fields
    """
    # Extract timestamp
    ts_str = data.get("ts", "")
    timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

    # Determine event type and extract curated fields
    event_type = data.get("event", "")
    tool_name = ""
    error_msg = ""
    agent_name = ""

    # Extract tool name from tool_call events
    if event_type == "tool_call" and "tool" in data:
        tool_name = data["tool"].get("name", "")

    # Extract error message from error events
    if event_type == "error" and "message" in data:
        error_msg = data["message"]

    # Extract agent name
    if "agent_id" in data:
        agent_name = data["agent_id"]

    return Event(
        id=data.get("event_id", str(timestamp)),
        type=event_type,
        timestamp=timestamp,
        agent_id=data.get("agent_id", ""),
        metadata=data,
        tool_name=tool_name,
        error_msg=error_msg,
        agent_name=agent_name,
    )


def _load_transcript(transcript_file: Path) -> list[TranscriptTurn]:
    """Load and parse transcript.jsonl.

    Args:
        transcript_file: Path to transcript.jsonl

    Returns:
        List of TranscriptTurn objects (includes corrupted records marked as such)
    """
    turns = []

    if not transcript_file.exists():
        return turns

    with open(transcript_file) as f:
        turn_id = 0
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
                # Extract timestamp if available
                timestamp_str = data.get("timestamp")
                timestamp = None
                if timestamp_str:
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    except (ValueError, AttributeError):
                        timestamp = None

                role = data.get("role", "")
                content = data.get("content", "")

                # Handle array content (e.g., thinking blocks + text)
                if isinstance(content, list):
                    # Check if there are multiple content types (thinking + text)
                    # If so, create separate turns for each
                    thinking_items = [
                        item for item in content if isinstance(item, dict) and item.get("type") == "thinking"
                    ]
                    text_items = [item for item in content if isinstance(item, dict) and item.get("type") == "text"]

                    # Separate thinking, tool_call, and text items
                    tool_call_items = [
                        item for item in content if isinstance(item, dict) and item.get("type") == "tool_call"
                    ]

                    # If we have any thinking or tool_call items, create separate turns
                    if thinking_items or tool_call_items:
                        # First, add thinking turn(s)
                        for thinking_item in thinking_items:
                            thinking_text = thinking_item.get("thinking", "")
                            # Truncate long thinking blocks for display
                            if len(thinking_text) > 200:
                                display_text = f"{thinking_text[:200]}..."
                            else:
                                display_text = thinking_text

                            turn = TranscriptTurn(
                                id=turn_id,
                                role=role,
                                content=display_text,
                                content_type="thinking",
                                timestamp=timestamp,
                                metadata={k: v for k, v in data.items() if k not in ("role", "content", "timestamp")},
                                collapsed=True,
                            )
                            turns.append(turn)
                            turn_id += 1

                        # Second, add text turn(s)
                        for text_item in text_items:
                            text = text_item.get("text", "")
                            turn = TranscriptTurn(
                                id=turn_id,
                                role=role,
                                content=text,
                                content_type="text",
                                timestamp=timestamp,
                                metadata={k: v for k, v in data.items() if k not in ("role", "content", "timestamp")},
                                collapsed=True,
                            )
                            turns.append(turn)
                            turn_id += 1

                        # Third, add tool_call turn(s)
                        for tool_call_item in tool_call_items:
                            # Extract tool name and id if available
                            tool_name = "unknown"
                            tool_id = ""
                            if isinstance(tool_call_item, dict):
                                tool_name = (
                                    tool_call_item.get("tool_call", {}).get("name", "unknown")
                                    if isinstance(tool_call_item.get("tool_call"), dict)
                                    else tool_call_item.get("name", "unknown")
                                )
                                tool_id = tool_call_item.get("id", "")

                            tool_metadata = {k: v for k, v in data.items() if k not in ("role", "content", "timestamp")}
                            tool_metadata["name"] = tool_name
                            if tool_id:
                                tool_metadata["tool_id"] = tool_id
                            # Store the full tool_call dict for expansion
                            tool_metadata["tool_call_data"] = tool_call_item

                            turn = TranscriptTurn(
                                id=turn_id,
                                role=role,
                                content="",
                                content_type="tool_call",
                                timestamp=timestamp,
                                metadata=tool_metadata,
                                collapsed=True,
                            )
                            turns.append(turn)
                            turn_id += 1
                    else:
                        # Single content type - convert array to readable text
                        content_parts = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get("type") == "text":
                                    content_parts.append(item.get("text", ""))
                                elif item.get("type") == "thinking":
                                    thinking_text = item.get("thinking", "")
                                    # Truncate long thinking blocks for display
                                    if len(thinking_text) > 200:
                                        content_parts.append(f"[thinking] {thinking_text[:200]}...")
                                    else:
                                        content_parts.append(f"[thinking] {thinking_text}")
                        content = "\n".join(content_parts) if content_parts else str(content)

                        turn = TranscriptTurn(
                            id=turn_id,
                            role=role,
                            content=content,
                            timestamp=timestamp,
                            metadata={k: v for k, v in data.items() if k not in ("role", "content", "timestamp")},
                            collapsed=True,
                        )
                        turns.append(turn)
                        turn_id += 1
                else:
                    # Simple string content
                    turn_metadata = {k: v for k, v in data.items() if k not in ("role", "content", "timestamp")}

                    # For tool executions, extract tool_call_id
                    if role == "tool" and "tool_call_id" in data:
                        turn_metadata["tool_id"] = data["tool_call_id"]

                    turn = TranscriptTurn(
                        id=turn_id,
                        role=role,
                        content=str(content) if content else "",
                        timestamp=timestamp,
                        metadata=turn_metadata,
                        collapsed=True,
                    )
                    turns.append(turn)
                    turn_id += 1

            except json.JSONDecodeError as e:
                # Invalid JSON - mark as corrupted
                turn = TranscriptTurn(
                    id=turn_id,
                    role="error",
                    content=line[:200],  # Show first 200 chars of the bad line
                    timestamp=None,
                    metadata={},
                    collapsed=False,
                    is_corrupted=True,
                    corruption_error=f"Invalid JSON: {str(e)[:100]}",
                )
                turns.append(turn)
                turn_id += 1
            except Exception as e:
                # Other validation errors (like ValidationError from Pydantic) - mark as corrupted
                turn = TranscriptTurn(
                    id=turn_id,
                    role="error",
                    content=line[:200],  # Show first 200 chars of the bad line
                    timestamp=None,
                    metadata={},
                    collapsed=False,
                    is_corrupted=True,
                    corruption_error=f"Validation error: {str(e)[:100]}",
                )
                turns.append(turn)
                turn_id += 1

    return turns

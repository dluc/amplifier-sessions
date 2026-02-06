"""Data types for Amplifier sessions."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Project(BaseModel):
    """Represents a project (collection of sessions)."""

    slug: str  # Folder slug (unique identifier)
    path: str  # Full path to project directory
    name: str = ""  # Project name from oldest root session metadata
    description: str = ""  # Project description from oldest root session metadata
    created_at: datetime  # When the oldest root session was created
    updated_at: datetime  # When the most recent session was updated
    session_count: int = 0  # Total number of sessions in this project
    working_dir: str = ""  # Working directory from oldest root session (user's project folder)

    # Root session names sorted chronologically (oldest first).
    # Populated by load_projects() from metadata.json of root sessions
    # (those with no parent_id) -- no event/transcript parsing needed.
    root_session_names: list[str] = Field(default_factory=list)


class Session(BaseModel):
    """Represents a single Amplifier session."""

    id: str
    slug: str  # Folder slug
    path: str  # Full path to session directory
    created_at: datetime
    updated_at: datetime
    title: str
    event_count: int
    malformed_count: int
    transcript_turns: int

    # Metadata fields (main sessions)
    bundle: str = ""
    model: str = ""
    working_dir: str = ""
    name: str = ""
    description: str = ""

    # Agent session fields
    parent_id: str = ""  # Parent session ID (for agent sessions)
    agent_name: str = ""  # Agent name (for agent sessions)
    agent_description: str = ""  # Agent description from agent_overlay.description
    agent_instruction: str = ""  # Agent instruction from agent_overlay.instruction

    # File sizes (in bytes)
    events_size: int = 0  # events.jsonl
    metadata_size: int = 0  # metadata.json
    transcript_size: int = 0  # transcript.jsonl

    # First and last transcript messages (for list-page preview).
    # Populated by load_session_metadata() which reads only the first and
    # last lines of transcript.jsonl -- never the full file.
    first_message_role: str = ""  # "user", "assistant", "tool", etc.
    first_message_content: str = ""  # Plain text (array content flattened)
    last_message_role: str = ""
    last_message_content: str = ""


class Event(BaseModel):
    """A single line from events.jsonl."""

    id: str
    type: str  # "tool_call", "response", "error", "delegate", etc.
    timestamp: datetime
    agent_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Curated fields (extracted for filtering/display)
    tool_name: str = ""
    error_msg: str = ""
    agent_name: str = ""


class TranscriptTurn(BaseModel):
    """A single user/assistant exchange."""

    id: int
    role: str  # "user", "assistant"
    content: str | list[dict[str, Any]]  # Can be string or array of content blocks
    content_type: str = "text"  # "text", "thinking", "tool_call"
    timestamp: datetime | None = None  # When this turn occurred
    metadata: dict[str, Any] = Field(default_factory=dict)
    collapsed: bool = True  # UI state: initially collapsed
    is_corrupted: bool = False  # True if record failed validation
    corruption_error: str = ""  # Error message if corrupted


@dataclass
class EventIndex:
    """Provides fast lookups for events."""

    by_type: dict[str, list[int]] = field(default_factory=dict)  # event type -> indices
    by_agent: dict[str, list[int]] = field(default_factory=dict)  # agent -> indices
    by_time: list[int] = field(default_factory=list)  # sorted by timestamp
    error_indices: list[int] = field(default_factory=list)  # events where type == "error"


@dataclass
class SessionData:
    """All parsed data for a session."""

    session: Session
    events: list[Event]
    index: EventIndex
    transcript: list[TranscriptTurn]

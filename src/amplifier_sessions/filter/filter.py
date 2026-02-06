"""Filter system for events."""

from dataclasses import dataclass
from datetime import datetime

from ..data.types import Event, EventIndex


@dataclass
class FilterRequest:
    """Request to filter events."""

    event_type: str = ""  # "tool_call,response,error"
    agent_names: str = ""  # "agent-a,agent-b"
    time_start: str = ""  # ISO 8601 or empty
    time_end: str = ""  # ISO 8601 or empty
    errors_only: bool = False
    tool_calls: bool = False
    contains: str = ""  # Text search


def apply_filters(
    events: list[Event],
    index: EventIndex,
    req: FilterRequest,
) -> list[int]:
    """Apply all active filters and return matching event indices.

    Args:
        events: List of all events
        index: EventIndex for fast lookups
        req: FilterRequest with filter criteria

    Returns:
        List of indices of events matching all filters
    """
    result = list(range(len(events)))  # Start with all event indices

    if req.event_type:
        result = _filter_by_type(events, result, req.event_type)

    if req.agent_names:
        result = _filter_by_agent(events, result, req.agent_names)

    if req.time_start or req.time_end:
        result = _filter_by_time(events, result, req.time_start, req.time_end)

    if req.errors_only:
        result = _filter_errors_only(events, result)

    if req.tool_calls:
        result = _filter_tool_calls(events, result)

    if req.contains:
        result = _filter_contains(events, result, req.contains)

    return result


def _filter_by_type(events: list[Event], indices: list[int], types_str: str) -> list[int]:
    """Filter events by type.

    Args:
        events: List of all events
        indices: Current matching indices
        types_str: Comma-separated event types (e.g., "tool_call,response")

    Returns:
        Filtered indices
    """
    if not types_str:
        return indices

    types = {t.strip() for t in types_str.split(",") if t.strip()}
    return [i for i in indices if events[i].type in types]


def _filter_by_agent(events: list[Event], indices: list[int], agents_str: str) -> list[int]:
    """Filter events by agent.

    Args:
        events: List of all events
        indices: Current matching indices
        agents_str: Comma-separated agent names or IDs

    Returns:
        Filtered indices
    """
    if not agents_str:
        return indices

    agents = {a.strip() for a in agents_str.split(",") if a.strip()}
    return [i for i in indices if events[i].agent_id in agents or events[i].agent_name in agents]


def _filter_by_time(
    events: list[Event],
    indices: list[int],
    time_start: str,
    time_end: str,
) -> list[int]:
    """Filter events by timestamp range.

    Args:
        events: List of all events
        indices: Current matching indices
        time_start: ISO 8601 start time or empty
        time_end: ISO 8601 end time or empty

    Returns:
        Filtered indices
    """
    start = None
    end = None

    if time_start:
        try:
            start = datetime.fromisoformat(time_start.replace("Z", "+00:00"))
        except ValueError:
            pass

    if time_end:
        try:
            end = datetime.fromisoformat(time_end.replace("Z", "+00:00"))
        except ValueError:
            pass

    if not start and not end:
        return indices

    result = []
    for i in indices:
        ts = events[i].timestamp
        if start and ts < start:
            continue
        if end and ts > end:
            continue
        result.append(i)

    return result


def _filter_errors_only(events: list[Event], indices: list[int]) -> list[int]:
    """Filter to show only errors.

    Args:
        events: List of all events
        indices: Current matching indices

    Returns:
        Filtered indices (events with type='error' or error_msg set)
    """
    return [i for i in indices if events[i].type == "error" or (events[i].error_msg and events[i].error_msg.strip())]


def _filter_tool_calls(events: list[Event], indices: list[int]) -> list[int]:
    """Filter to show only tool calls.

    Args:
        events: List of all events
        indices: Current matching indices

    Returns:
        Filtered indices (events with type='tool_call')
    """
    return [i for i in indices if events[i].type == "tool_call"]


def _filter_contains(events: list[Event], indices: list[int], text: str) -> list[int]:
    """Filter events by text content.

    Args:
        events: List of all events
        indices: Current matching indices
        text: Text to search for (case-insensitive)

    Returns:
        Filtered indices (events containing text)
    """
    if not text:
        return indices

    text_lower = text.lower()

    def event_matches(event: Event) -> bool:
        """Check if event content matches search text."""
        # Search in curated fields
        if text_lower in event.type.lower():
            return True
        if text_lower in event.tool_name.lower():
            return True
        if text_lower in event.error_msg.lower():
            return True
        if text_lower in event.agent_name.lower():
            return True
        # Search in metadata string representation
        if text_lower in str(event.metadata).lower():
            return True
        return False

    return [i for i in indices if event_matches(events[i])]

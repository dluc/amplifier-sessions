"""Tests for filter system."""

from datetime import datetime, timedelta

from amplifier_sessions.data.types import Event, EventIndex
from amplifier_sessions.filter.filter import FilterRequest, apply_filters


def _mock_event(
    id: str,
    type: str,
    timestamp: datetime,
    agent_id: str = "",
    tool_name: str = "",
    error_msg: str = "",
    metadata: dict | None = None,
) -> Event:
    """Create a mock event."""
    if metadata is None:
        metadata = {}
    return Event(
        id=id,
        type=type,
        timestamp=timestamp,
        agent_id=agent_id,
        tool_name=tool_name,
        error_msg=error_msg,
        metadata=metadata,
    )


def _mock_index(events: list[Event]) -> EventIndex:
    """Create a mock event index."""
    return EventIndex(
        by_type={},
        by_agent={},
        by_time=list(range(len(events))),
        error_indices=[i for i, e in enumerate(events) if e.type == "error"],
    )


def test_filter_empty_list():
    """Test filtering empty event list."""
    events = []
    index = _mock_index(events)
    indices = apply_filters(events, index, FilterRequest())

    assert indices == []


def test_filter_all_match_no_filters():
    """Test that all events match when no filters applied."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now, agent_id="agent-a"),
        _mock_event("2", "response", now, agent_id="agent-b"),
        _mock_event("3", "error", now, agent_id="agent-a"),
    ]
    index = _mock_index(events)
    indices = apply_filters(events, index, FilterRequest())

    assert indices == [0, 1, 2]


def test_filter_by_type_single():
    """Test filtering by single event type."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now),
        _mock_event("2", "response", now),
        _mock_event("3", "tool_call", now),
    ]
    index = _mock_index(events)
    request = FilterRequest(event_type="tool_call")
    indices = apply_filters(events, index, request)

    assert indices == [0, 2]


def test_filter_by_type_multiple():
    """Test filtering by multiple event types."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now),
        _mock_event("2", "response", now),
        _mock_event("3", "error", now),
        _mock_event("4", "tool_call", now),
    ]
    index = _mock_index(events)
    request = FilterRequest(event_type="tool_call,error")
    indices = apply_filters(events, index, request)

    assert sorted(indices) == [0, 2, 3]


def test_filter_by_agent_single():
    """Test filtering by single agent."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now, agent_id="agent-a"),
        _mock_event("2", "response", now, agent_id="agent-b"),
        _mock_event("3", "tool_call", now, agent_id="agent-a"),
    ]
    index = _mock_index(events)
    request = FilterRequest(agent_names="agent-a")
    indices = apply_filters(events, index, request)

    assert indices == [0, 2]


def test_filter_by_agent_multiple():
    """Test filtering by multiple agents."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now, agent_id="agent-a"),
        _mock_event("2", "response", now, agent_id="agent-b"),
        _mock_event("3", "tool_call", now, agent_id="agent-c"),
    ]
    index = _mock_index(events)
    request = FilterRequest(agent_names="agent-a,agent-c")
    indices = apply_filters(events, index, request)

    assert sorted(indices) == [0, 2]


def test_filter_by_time_range():
    """Test filtering by timestamp range."""
    now = datetime.now()
    earlier = now - timedelta(hours=1)
    later = now + timedelta(hours=1)

    events = [
        _mock_event("1", "tool_call", earlier),
        _mock_event("2", "response", now),
        _mock_event("3", "tool_call", later),
    ]
    index = _mock_index(events)

    request = FilterRequest(time_start=now.isoformat(), time_end=later.isoformat())
    indices = apply_filters(events, index, request)

    # Should include events at or after time_start
    assert 1 in indices
    assert 2 in indices
    assert 0 not in indices


def test_filter_errors_only():
    """Test filtering errors only."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now),
        _mock_event("2", "error", now, error_msg="Something failed"),
        _mock_event("3", "response", now),
        _mock_event("4", "error", now, error_msg="Another failure"),
    ]
    index = _mock_index(events)
    request = FilterRequest(errors_only=True)
    indices = apply_filters(events, index, request)

    assert indices == [1, 3]


def test_filter_tool_calls_only():
    """Test filtering tool calls only."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now, tool_name="bash"),
        _mock_event("2", "response", now),
        _mock_event("3", "tool_call", now, tool_name="filesystem"),
    ]
    index = _mock_index(events)
    request = FilterRequest(tool_calls=True)
    indices = apply_filters(events, index, request)

    assert indices == [0, 2]


def test_filter_contains_text():
    """Test filtering by text content."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now, metadata={"content": "hello world"}),
        _mock_event("2", "response", now, metadata={"content": "goodbye"}),
        _mock_event("3", "tool_call", now, metadata={"content": "hello there"}),
    ]
    index = _mock_index(events)
    request = FilterRequest(contains="hello")
    indices = apply_filters(events, index, request)

    assert sorted(indices) == [0, 2]


def test_filter_combined():
    """Test combining multiple filters."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now, agent_id="agent-a"),
        _mock_event("2", "response", now, agent_id="agent-a"),
        _mock_event("3", "tool_call", now, agent_id="agent-b"),
        _mock_event("4", "response", now, agent_id="agent-b"),
    ]
    index = _mock_index(events)
    # Only tool_calls from agent-a
    request = FilterRequest(event_type="tool_call", agent_names="agent-a")
    indices = apply_filters(events, index, request)

    assert indices == [0]


def test_filter_no_matches():
    """Test filter with no matching events."""
    now = datetime.now()
    events = [
        _mock_event("1", "tool_call", now),
        _mock_event("2", "response", now),
    ]
    index = _mock_index(events)
    request = FilterRequest(event_type="error")
    indices = apply_filters(events, index, request)

    assert indices == []

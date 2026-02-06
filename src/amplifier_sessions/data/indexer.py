"""Build in-memory indexes for fast event filtering."""

from .types import Event, EventIndex


def build_index(events: list[Event]) -> EventIndex:
    """Build indexes for fast filtering.

    Args:
        events: List of Event objects

    Returns:
        EventIndex with fast lookup structures
    """
    index = EventIndex()

    # Index by event type
    for i, event in enumerate(events):
        if event.type not in index.by_type:
            index.by_type[event.type] = []
        index.by_type[event.type].append(i)

    # Index by agent
    for i, event in enumerate(events):
        if event.agent_id:
            if event.agent_id not in index.by_agent:
                index.by_agent[event.agent_id] = []
            index.by_agent[event.agent_id].append(i)

    # Index errors
    for i, event in enumerate(events):
        if event.type == "error" or event.error_msg:
            index.error_indices.append(i)

    # Build time index (sorted)
    index.by_time = sorted(range(len(events)), key=lambda i: events[i].timestamp)

    return index

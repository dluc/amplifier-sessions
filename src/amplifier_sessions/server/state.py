"""Application state management."""

import threading
from collections import OrderedDict

from ..data.types import SessionData

# ---------------------------------------------------------------------------
# Rendered-page LRU cache
#
# PERFORMANCE: The projects list and project detail pages are expensive to
# build -- load_projects() reads metadata.json for every session across all
# projects, and load_sessions_metadata_for_slug() counts lines in every
# events/transcript file.  These operations are I/O-bound and take seconds
# for large projects.
#
# Caching the final rendered HTML (not the raw data) keeps memory usage
# tiny -- a rendered HTML page is typically 5-50KB -- while skipping all
# the filesystem I/O, template rendering, and tree-flattening logic on
# repeat visits.
#
# Session detail pages are NOT cached: they need fresh data (the user may
# be actively running a session), and each page can be large (full
# event/transcript HTML).
#
# Uses OrderedDict for O(1) LRU: move_to_end() on hit, popitem(last=False)
# on eviction.  Thread-safe via the same RLock used for session state.
# ---------------------------------------------------------------------------

_DEFAULT_PAGE_CACHE_MAX = 20


class PageCache:
    """LRU cache for rendered HTML pages.

    Stores rendered HTML strings keyed by URL path.  Bounded by max_size
    with least-recently-used eviction.

    Thread safety: callers must hold AppState._lock before calling any
    method (AppState methods already do this).
    """

    def __init__(self, max_size: int = _DEFAULT_PAGE_CACHE_MAX) -> None:
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._max_size = max_size

    def get(self, key: str) -> str | None:
        """Get a cached page, promoting it to most-recently-used.

        Returns None on cache miss.
        """
        if key not in self._cache:
            return None
        # LRU promotion: move to end (most recently used)
        self._cache.move_to_end(key)
        return self._cache[key]

    def put(self, key: str, html: str) -> None:
        """Cache a rendered page, evicting LRU entry if at capacity."""
        if key in self._cache:
            # Update existing entry and promote to MRU
            self._cache.move_to_end(key)
            self._cache[key] = html
        else:
            # Evict least-recently-used if at capacity
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = html

    def invalidate(self, key: str) -> None:
        """Remove a specific entry from the cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Remove all cached pages."""
        self._cache.clear()

    @property
    def size(self) -> int:
        """Current number of cached pages."""
        return len(self._cache)


class AppState:
    """Manages application state (loaded sessions for all projects)."""

    def __init__(self) -> None:
        """Initialize empty app state."""
        self.sessions: dict[str, SessionData] = {}  # All sessions keyed by session_id
        self.sessions_by_slug: dict[str, list[SessionData]] = {}  # Sessions grouped by project slug
        self.current_slug: str = ""  # Current working directory slug
        self.session_paths: dict[str, str] = {}  # Map of session_id -> path (for on-demand loading)
        self._lock = threading.RLock()

        # LRU cache for rendered HTML pages (projects list + project detail).
        # Caches the final HTML string, NOT raw data -- keeps memory small
        # (~5-50KB per page) while avoiding repeated filesystem I/O and
        # template rendering.  NOT used for session detail pages.
        self.page_cache = PageCache(max_size=_DEFAULT_PAGE_CACHE_MAX)

    def get_session(self, session_id: str) -> SessionData | None:
        """Get a session by ID.

        Args:
            session_id: Session ID to retrieve

        Returns:
            SessionData if found, None otherwise
        """
        with self._lock:
            return self.sessions.get(session_id)

    def add_session(self, session_id: str, session: SessionData, slug: str) -> None:
        """Add a session to state.

        Args:
            session_id: Session ID
            session: SessionData to add
            slug: Project slug this session belongs to
        """
        with self._lock:
            self.sessions[session_id] = session
            if slug not in self.sessions_by_slug:
                self.sessions_by_slug[slug] = []
            self.sessions_by_slug[slug].append(session)

    def list_sessions(self) -> list[SessionData]:
        """List all loaded sessions.

        Returns:
            List of SessionData objects
        """
        with self._lock:
            return list(self.sessions.values())

    def list_sessions_for_slug(self, slug: str) -> list[SessionData]:
        """List sessions for a specific project slug.

        Args:
            slug: Project slug to filter by

        Returns:
            List of SessionData objects for that slug
        """
        with self._lock:
            return self.sessions_by_slug.get(slug, [])

    # --- Rendered-page cache methods (thread-safe wrappers) ---------------

    def get_cached_page(self, path: str) -> str | None:
        """Get a cached rendered HTML page by URL path.

        Returns None on cache miss.  Promotes the entry to
        most-recently-used on hit.
        """
        with self._lock:
            return self.page_cache.get(path)

    def cache_page(self, path: str, html: str) -> None:
        """Cache a rendered HTML page.  Evicts LRU if at capacity."""
        with self._lock:
            self.page_cache.put(path, html)

    def invalidate_cached_page(self, path: str) -> None:
        """Remove a specific page from the cache."""
        with self._lock:
            self.page_cache.invalidate(path)

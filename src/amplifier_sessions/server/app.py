"""Flask application setup and routing."""

import os
import platform
import subprocess
from pathlib import Path

from flask import Flask, render_template, request

from ..data.loader import load_projects, load_session, load_sessions_metadata_for_slug
from ..filter import FilterRequest, apply_filters
from ..utils import format_bytes, format_datetime_local, humanize_time_diff, shorten_path
from .state import AppState


def create_app(app_state: AppState) -> Flask:
    """Create and configure Flask application.

    Args:
        app_state: Application state with loaded sessions

    Returns:
        Configured Flask app
    """
    # Resolve template and static folders relative to this file
    base_dir = os.path.dirname(__file__)
    template_dir = os.path.join(base_dir, "..", "web", "templates")
    static_dir = os.path.join(base_dir, "..", "web", "static")

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )

    app.config["app_state"] = app_state

    # Register Jinja2 filters for template use
    app.jinja_env.filters["humanize"] = humanize_time_diff
    app.jinja_env.filters["format_datetime"] = format_datetime_local
    app.jinja_env.filters["format_bytes"] = format_bytes
    app.jinja_env.filters["shorten_path"] = shorten_path

    @app.route("/")
    def landing():
        """Landing page - describes the app and links to projects."""
        return render_template("landing.html")

    @app.route("/projects/")
    def projects_list():
        """List all projects.

        PERFORMANCE: The rendered HTML is cached in AppState.page_cache.
        load_projects() reads metadata.json for every session across all
        projects, which adds up for large collections.  The cached HTML
        is typically 5-50KB -- far cheaper than re-scanning the filesystem.

        The cache is invalidated implicitly by LRU eviction (max 20
        entries).  For a forced refresh the user can restart the server
        or we can add a cache-bust mechanism later if needed.
        """
        cache_key = "/projects/"
        cached = app_state.get_cached_page(cache_key)
        if cached is not None:
            return cached

        from datetime import datetime, timezone

        projects = load_projects()
        html = render_template("projects.html", projects=projects, now=datetime.now(timezone.utc))
        app_state.cache_page(cache_key, html)
        return html

    @app.route("/projects/<slug>")
    def project_detail(slug: str):
        """Show all sessions for a specific project slug.

        PERFORMANCE: Two layers of optimisation protect this route:

        1. **Page cache** (this layer) -- the rendered HTML is cached in
           AppState.page_cache (LRU, max 20 entries).  On cache hit the
           response is returned immediately with zero filesystem I/O.

        2. **Metadata-only loading** (cache miss path) -- uses
           load_sessions_metadata_for_slug() which reads only
           metadata.json + counts lines via fast binary reading.  It
           never parses event or transcript JSON.  Completes in ~2-5s
           even for 5GB of events (vs ~60s with the old full-parse path).

        Session detail pages are NOT cached -- they need fresh data and
        can be large.
        """
        cache_key = f"/projects/{slug}"
        cached = app_state.get_cached_page(cache_key)
        if cached is not None:
            return cached

        sessions = load_sessions_metadata_for_slug(slug)

        if not sessions:
            return "Project not found", 404

        # Render the project sessions page
        sessions_by_id = {s.id: s for s in sessions}
        main_sessions = [s for s in sessions if not s.parent_id]
        main_sessions.sort(key=lambda s: s.updated_at, reverse=True)

        def flatten_sessions_tree(session_id, sessions_map, nesting_level=0):
            """Recursively flatten sessions with their parent-child relationships."""
            result = []
            session = sessions_map.get(session_id)
            if not session:
                return result

            result.append((session, nesting_level))
            children = [s for s in sessions if s.parent_id == session_id]
            children.sort(key=lambda s: s.updated_at, reverse=True)

            for child in children:
                result.extend(flatten_sessions_tree(child.id, sessions_map, nesting_level + 1))

            return result

        organized_sessions = []
        for main_session in main_sessions:
            organized_sessions.extend(flatten_sessions_tree(main_session.id, sessions_by_id))

        sessions_dir = ""
        if main_sessions:
            sessions_dir = str(Path(main_sessions[0].path).parent)

        html = render_template(
            "list.html",
            sessions=organized_sessions,
            slug=slug,
            sessions_dir=sessions_dir,
        )
        app_state.cache_page(cache_key, html)
        return html

    @app.route("/projects/<slug>/sessions")
    def project_sessions_redirect(slug: str):
        """Redirect /projects/<slug>/sessions to /projects/<slug>."""
        return project_detail(slug)

    @app.route("/api/open-dir", methods=["POST"])
    def open_directory():
        """Open a directory in the system file explorer."""
        try:
            data = request.get_json()
            if not data or "path" not in data:
                return {"error": "Missing path parameter"}, 400

            dir_path = data["path"]
            dir_path_obj = Path(dir_path)
            if not dir_path_obj.exists():
                return {"error": f"Directory does not exist: {dir_path}"}, 404

            system = platform.system()
            if system == "Darwin":  # macOS
                subprocess.Popen(["open", str(dir_path_obj)])
            elif system == "Windows":
                subprocess.Popen(["explorer", str(dir_path_obj)])
            elif system == "Linux":
                subprocess.Popen(["xdg-open", str(dir_path_obj)])

            return {"status": "opened"}, 200
        except Exception as e:
            return {"error": str(e)}, 500

    @app.route("/session/<session_id>", methods=["GET", "POST"])
    def session_detail(session_id: str):
        """Session detail page with filters.

        Unlike project_detail(), this route DOES need the full parsed
        event and transcript data (for timeline display, filtering, etc.)
        so it calls load_session() -- the expensive path.

        Results are cached in AppState so subsequent visits / HTMX
        partial requests don't re-parse.  The first visit to a large
        session will take several seconds; after that it's instant.
        """
        # Check AppState cache first (populated by a previous visit)
        session_data = app_state.get_session(session_id)
        if not session_data:
            # Cache miss -- load full session data on-demand.
            # load_session() is expensive (~12s/GB) but we need the full
            # events + transcript for this page.  Cache after loading.
            if session_id in app_state.session_paths:
                try:
                    session_data = load_session(app_state.session_paths[session_id])
                    app_state.add_session(session_id, session_data, app_state.current_slug)
                except Exception as e:
                    return f"Failed to load session: {e}", 500
            else:
                return "Session not found", 404

        # Get the view mode from query params (default: transcript)
        view = request.args.get("view", "transcript")
        is_htmx_request = request.headers.get("HX-Request") == "true"

        if request.method == "POST":
            # Handle filter submission (HTMX)
            req = FilterRequest(
                event_type=request.form.get("event_type", ""),
                agent_names=request.form.get("agent_names", ""),
                time_start=request.form.get("time_start", ""),
                time_end=request.form.get("time_end", ""),
                errors_only=request.form.get("errors_only") == "on",
                tool_calls=request.form.get("tool_calls") == "on",
                contains=request.form.get("contains", ""),
            )

            event_indices = apply_filters(session_data.events, session_data.index, req)

            # Return filtered timeline partial
            return render_template(
                "timeline.html",
                events=session_data.events,
                event_indices=event_indices,
                count=len(event_indices),
            )

        # GET: Check if this is an HTMX view-switch request
        event_indices = list(range(len(session_data.events)))

        if is_htmx_request:
            # Return only the main content (partial) for view switching
            return render_template(
                "session_content.html",
                session=session_data.session,
                events=session_data.events,
                event_indices=event_indices,
                transcript=session_data.transcript,
                view=view,
            )
        else:
            # Return full page for initial load
            return render_template(
                "session.html",
                session=session_data.session,
                slug=session_data.session.slug,
                events=session_data.events,
                event_indices=event_indices,
                transcript=session_data.transcript,
                view=view,
            )

    return app

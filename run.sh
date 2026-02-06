#!/usr/bin/env bash
# Launch Amplifier Session Viewer
# 
# The app auto-detects the directory type:
#   - If it contains sessions/ subdirectories → loads from there
#   - Otherwise → calculates slug and scans ~/.amplifier/projects/<slug>/sessions/
#
# Usage:
#   ./run.sh                                      # Use current directory
#   ./run.sh --dir /path/to/project               # Specify project directory
#   ./run.sh --dir ~/.amplifier/projects/<slug>/sessions  # Or specify sessions directory directly
#   ./run.sh --port 8080                          # Use custom port
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Clear caches
echo "🧹 Clearing caches..."
find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "$SCRIPT_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
find "$SCRIPT_DIR" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
find "$SCRIPT_DIR" -type d -name ".egg-info" -exec rm -rf {} + 2>/dev/null || true
rm -rf "$SCRIPT_DIR/.cache" 2>/dev/null || true
export PYTHONDONTWRITEBYTECODE=1

# Check if virtual environment exists
if [ ! -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    echo "✗ Virtual environment not found. Creating..."
    cd "$SCRIPT_DIR"
    uv venv
    source .venv/bin/activate
    uv pip install -e .
    echo "✓ Virtual environment created and installed"
else
    source "$SCRIPT_DIR/.venv/bin/activate"
    # Reinstall in development mode to ensure latest code
    cd "$SCRIPT_DIR"
    uv pip install -e . --force-reinstall --no-deps
fi

# Disable Flask caching
export FLASK_ENV=development
export FLASK_DEBUG=1
export SEND_FILE_MAX_AGE_DEFAULT=0

echo "✓ Ready to launch..."
# Run amplifier-sessions with all passed arguments
exec amplifier-sessions "$@"

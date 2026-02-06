#!/usr/bin/env bash

set -e

uvx --from git+https://github.com/dluc/amplifier-sessions.git python -m amplifier_sessions "$@"

#!/usr/bin/env bash
# Run the test suite. Standard library only - no pytest, no dependencies.
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -m unittest discover -s tests -t . -v "$@"

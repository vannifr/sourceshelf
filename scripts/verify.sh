#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m compileall -q sourceshelf scripts tests
ruff check .
python3 -m pytest -q --cov=sourceshelf --cov-fail-under=95
python3 -m sourceshelf --root examples/library check

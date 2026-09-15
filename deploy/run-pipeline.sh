#!/usr/bin/env bash
# The daily job. Runs under flock via factline.service — never invoke directly
# in production, or you lose the concurrency guarantee.
set -euo pipefail

cd /opt/factline
PY=/opt/factline/.venv/bin/python
SITE=/opt/factline/data/site
mkdir -p "$SITE"

started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "pipeline.start $started"

# --- ingest ----------------------------------------------------------------
"$PY" -m factline.cli ingest

# --- report build ----------------------------------------------------------
# TODO(Day 6): replace with `factline build-site`, which renders the HTML
# reports into $SITE. Until then the coverage table is the visible output.
"$PY" -m factline.cli coverage

# --- heartbeat -------------------------------------------------------------
# The status page reads this. A pipeline that exits 0 but has not refreshed its
# data in three days is more dangerous than one that crashes loudly, so
# freshness is published as a first-class signal rather than inferred from logs.
finished=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "$SITE/health.json" <<JSON
{
  "last_run_started": "$started",
  "last_run_finished": "$finished",
  "git_sha": "$(git -C /opt/factline rev-parse --short HEAD)",
  "status": "ok"
}
JSON

echo "pipeline.done $finished"

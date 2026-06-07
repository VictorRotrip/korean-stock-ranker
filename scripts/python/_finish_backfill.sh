#!/bin/bash
set -u
DIRECT_URL=$(grep ^DATABASE_URL2= ~/Downloads/korean-stock-ranker/.env.local | cut -d= -f2-)
export DATABASE_URL="$DIRECT_URL"
DATES="2025-08-29 2025-09-30 2025-10-31 2025-11-28 2025-12-30 2026-01-30 2026-02-27 2026-03-31 2026-04-30 2026-05-29"
for DATE in $DATES; do
    echo "=== STARTING $DATE at $(date) ==="
    python -u calculate_factors.py --universe krx_all_historical --as-of-date "$DATE" --require-pit-market-cap
    rc=$?
    if [ $rc -ne 0 ]; then
        echo "!!! calculate_factors FAILED for $DATE (exit $rc); skipping ranking step"
        continue
    fi
    python -u run_ranking_snapshot.py --universe krx_all_historical --as-of-date "$DATE" --system-id p123-inspired --missing-category-policy neutral --min-active-weight-coverage 0.60 --min-category-count 3 --min-factor-count 10 --require-pit-market-cap
    echo "=== DONE $DATE at $(date) ==="
done

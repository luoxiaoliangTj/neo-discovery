#!/bin/bash
# Check NEO dashboard freshness — exit 1 if stale
DB="/home/lxl/src/neo_catalog.db"

# Check scan_log for any successful refresh in last 18 hours
RESULT=$(sqlite3 "$DB" "SELECT COUNT(*) FROM scan_log WHERE scan_time > datetime('now', '-18 hours') AND scan_type IN ('sbdb_refresh', 'daily_discovery', 'neocp_scan') AND status='success'" 2>/dev/null)

if [ "$RESULT" -gt 0 ]; then
    echo "OK: DB refreshed recently, scan_log entries=$RESULT"
    exit 0
else
    # Fallback: check last updated_at vs today (Beijing time)
    LAST=$(sqlite3 "$DB" "SELECT MAX(updated_at) FROM neo_catalog" 2>/dev/null)
    NOW_BJ=$(TZ=Asia/Shanghai date +%Y-%m-%d)
    # Convert UTC timestamp to Beijing date using python for reliability
    LAST_BJ=$(python3 -c "from datetime import datetime, timedelta; t=datetime.fromisoformat('$LAST'.replace('Z','+00:00').replace('+00:00','')); print((t+timedelta(hours=8)).strftime('%Y-%m-%d'))" 2>/dev/null)
    
    if [ "$LAST_BJ" = "$NOW_BJ" ]; then
        echo "OK: DB timestamps today (fallback). last=$LAST"
        exit 0
    else
        echo "STALE: No refresh today. scan_log entries=$RESULT, last_updated=$LAST (Beijing: $LAST_BJ vs $NOW_BJ)"
        exit 1
    fi
fi

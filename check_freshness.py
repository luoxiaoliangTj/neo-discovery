#!/usr/bin/env python3
"""Check NEO dashboard freshness — exit 1 if stale.
Replaces bash version since sqlite3 CLI not installed here.
"""
import sqlite3
from datetime import datetime, timedelta
import sys

DB = "/home/lxl/src/neo_catalog.db"

db = sqlite3.connect(DB)
c = db.cursor()

# Check scan_log for any successful refresh in last 18 hours
c.execute("""SELECT COUNT(*) FROM scan_log 
    WHERE scan_time > datetime('now', '-18 hours') 
    AND scan_type IN ('sbdb_refresh', 'daily_discovery', 'neocp_scan') 
    AND status='success'""")
scan_count = c.fetchone()[0]

if scan_count > 0:
    print(f"OK: DB refreshed recently, scan_log entries={scan_count}")
    db.close()
    sys.exit(0)

# Fallback: check last updated_at vs today (Beijing time)
c.execute("SELECT MAX(updated_at) FROM neo_catalog")
last = c.fetchone()[0]

now_bj = (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d')

if last:
    last_dt = datetime.fromisoformat(last.replace('Z', '+00:00').replace('+00:00', ''))
    last_bj = (last_dt + timedelta(hours=8)).strftime('%Y-%m-%d')
    
    if last_bj == now_bj:
        print(f"OK: DB timestamps today (fallback). last={last} (Beijing: {last_bj})")
        db.close()
        sys.exit(0)
    else:
        print(f"STALE: No refresh today. scan_log={scan_count}, last_updated={last} (Beijing: {last_bj} vs {now_bj})")
        db.close()
        sys.exit(1)
else:
    print("STALE: No records in catalog at all!")
    db.close()
    sys.exit(1)

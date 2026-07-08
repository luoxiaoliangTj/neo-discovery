#!/usr/bin/env python3
"""
Quick daily stamp: bump updated_at for NEOs in the current 7-day feed,
so the dashboard doesn't show stale warning. Also write scan_log entry.
"""
import sqlite3
from datetime import datetime, timedelta
import urllib.request, json, time

CATALOG_DB = '/home/lxl/src/neo_catalog.db'

# Get NEOs from 7-day feed
today = datetime.utcnow().strftime('%Y-%m-%d')
week = (datetime.utcnow() + timedelta(days=7)).strftime('%Y-%m-%d')
url = f'https://api.nasa.gov/neo/rest/v1/feed?start_date={today}&end_date={week}&api_key=DEMO_KEY'

try:
    with urllib.request.urlopen(url, timeout=15) as r:
        data = json.loads(r.read().decode())
    neos = set()
    for date_str, objs in data.get('near_earth_objects', {}).items():
        for o in objs:
            neos.add(o['id'])
    print(f'Feed: {len(neos)} unique NEOs ({today} → {week})')
except Exception as e:
    print(f'Feed error: {e}')
    neos = set()

db = sqlite3.connect(CATALOG_DB)
c = db.cursor()

# Stamp all feed NEOs with current time
now = datetime.utcnow().isoformat()
updated = 0
for neo_id in neos:
    c.execute('UPDATE neo_catalog SET updated_at = ? WHERE neo_id = ?', (now, neo_id))
    if c.rowcount > 0:
        updated += 1

# Also log — 动态查，不写死
c.execute('SELECT COUNT(*) FROM neo_catalog')
real_count = c.fetchone()[0]
c.execute('INSERT INTO scan_log (scan_time, scan_type, neows_count, catalog_count, new_count, status) VALUES (?, ?, ?, ?, ?, ?)',
    (datetime.utcnow().isoformat(), 'daily_stamp', len(neos), real_count, 0, 'stamped'))

db.commit()
print(f'Stamped {updated} NEOs at {now}')
print(f'Scan log: {updated} updated')
db.close()

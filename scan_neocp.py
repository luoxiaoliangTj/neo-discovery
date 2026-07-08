#!/usr/bin/env python3
"""
MPC NEOCP Scanner — scan the MPC NEO Confirmation Page for new candidates
and add them to the neo_confirmation_tracker.db.
"""
import sqlite3
import urllib.request
import re
import json
from datetime import datetime

TRACKER_DB = '/home/lxl/src/neo_confirmation_tracker.db'
NEOCP_URL = 'https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html'


def fetch_neocp():
    """Fetch NEOCP tabular page and return raw HTML."""
    req = urllib.request.Request(NEOCP_URL, headers={'User-Agent': 'HermesAgent/1.0 (NEO-research)'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', errors='replace')


def parse_candidates(html):
    """Parse NEOCP candidate table into list of dicts.
    
    MPC NEOCP uses malformed HTML where <tr> rows are NOT closed with </tr>.
    Each new <tr> implicitly closes the previous row.
    Strategy: split on <tr> and parse each block.
    """
    candidates = []
    
    # Split on <tr> tags
    tr_blocks = re.split(r'<tr[^>]*>', html)
    
    for block in tr_blocks:
        block = block.strip()
        if not block:
            continue
        
        # Extract designation from checkbox VALUE
        desig_match = re.search(r'<input[^>]*type="checkbox"[^>]*name="obj"[^>]*VALUE="([A-Za-z0-9]{5,8})"[^>]*/?>', block)
        if not desig_match:
            continue
        desig = desig_match.group(1)
        
        # Skip false positives
        if len(desig) < 5:
            continue
        # Must look like an MPC designation (mix of letters and digits)
        if not re.search(r'[A-Za-z]', desig) or not re.search(r'\d', desig):
            continue
        
        # Extract all cells (strip tags)
        cells = re.findall(r'<td[^>]*>(.*?)</td>', block, re.DOTALL)
        if len(cells) < 3:
            continue
        
        # Clean cell contents
        clean_cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in cells]
        
        # obs_count: second cell, usually "100" or "84"
        obs_count = clean_cells[1].strip() if len(clean_cells) > 1 else '100'
        if not re.match(r'^\d+$', obs_count):
            obs_count = '100'
        
        # obs_date: look for "2026 07 05.0" pattern in the cells
        obs_date = ''
        for cell in clean_cells:
            date_match = re.search(r'20\d{2}\s+0?\d{1,2}\s+[\d.]+', cell)
            if date_match:
                # Format as YYYYMMDD.D
                parts = date_match.group().split()
                if len(parts) >= 3:
                    obs_date = f"{parts[0]}{int(parts[1]):02d}{parts[2]}"
                break
        
        candidates.append({
            'internal_id': desig,
            'obs_count': obs_count,
            'obs_date': obs_date,
        })
    
    # Deduplicate
    seen = set()
    unique = []
    for c in candidates:
        if c['internal_id'] not in seen:
            seen.add(c['internal_id'])
            unique.append(c)
    
    return unique


def scan_and_update():
    """Fetch NEOCP, compare with tracker DB, add new ones."""
    now = datetime.utcnow().isoformat()
    print(f"=== MPC NEOCP Scanner ===")
    print(f"Time: {now}")
    
    # Fetch page
    try:
        html = fetch_neocp()
        print(f"Fetched NEOCP: {len(html)} bytes")
    except Exception as e:
        print(f"FETCH ERROR: {e}")
        return
    
    # Parse candidates  
    candidates = parse_candidates(html)
    print(f"Parsed {len(candidates)} candidates from NEOCP")
    
    if not candidates:
        print("No candidates found (page format may have changed)")
        return
    
    # Check which are new
    db = sqlite3.connect(TRACKER_DB)
    c = db.cursor()
    c.execute("SELECT internal_id FROM candidate_tracking")
    existing = set(row[0] for row in c.fetchall())
    print(f"Existing in tracker: {len(existing)}")
    
    new_count = 0
    for cand in candidates:
        desig = cand['internal_id']
        if desig not in existing:
            c.execute('''INSERT INTO candidate_tracking 
                (internal_id, first_seen_date, last_seen_date, observer_code, obs_date, 
                 ra, dec, mag, obs_count, arc_days, status, nasa_neo_id, nasa_name, 
                 nasa_designation, confirmed_date, early_days, json_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (desig, now, now, cand.get('observer_code', '100'), cand.get('obs_date', ''),
                 '', '', '', cand.get('obs_count', '100'), '0', 'pending',
                 None, None, None, None, None, None))
            c.execute('''INSERT INTO tracking_log (timestamp, event_type, internal_id, details)
                VALUES (?, ?, ?, ?)''',
                (now, 'NEW_CANDIDATE', desig, json.dumps(cand)))
            new_count += 1
            print(f"  NEW: {desig} (obs={cand.get('obs_count','?')}, date={cand.get('obs_date','?')})")
    
    db.commit()
    print(f"\nAdded {new_count} new candidates")
    print(f"Total pending tracking: {len(existing) + new_count}")

    # 写入 scan_log (在 catalog DB 中)
    conn2 = sqlite3.connect('/home/lxl/src/neo_catalog.db')
    c2 = conn2.cursor()
    c2.execute('INSERT INTO scan_log (scan_time, scan_type, neows_count, catalog_count, new_count, status) VALUES (?, ?, ?, ?, ?, ?)',
        (now, 'neocp_scan', len(candidates), len(candidates), new_count, 'success' if new_count > 0 else 'success'))
    conn2.commit()
    conn2.close()
    db.close()


if __name__ == '__main__':
    scan_and_update()

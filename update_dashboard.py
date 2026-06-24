#!/usr/bin/env python3
"""
NEO Dashboard Data Update Pipeline
====================================
Fetches real-time NASA NEOWS feed (7-day close approaches),
MPC NEO Confirmation Page candidates, reads local catalog/tracker stats,
and generates updated data.js for the dashboard.

Does NOT push to GitHub — dashboard served from gh-pages branch.
"""

import json
import sqlite3
import sys
import os
import time
from datetime import datetime, timedelta

# Try to import requests, fall back to urllib
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.parse
    import urllib.error
    HAS_REQUESTS = False

# ============================================================
# Configuration
# ============================================================
CONFIG = {
    'nasa_api_key': os.environ.get('NASA_API_KEY', 'oI6kUNRErbojDSSt8Xnma6OA2UsZQAmoCOA6Tkc3'),
    'nasa_feed_url': 'https://api.nasa.gov/neo/rest/v1/feed',
    'catalog_db': '/home/lxl/src/neo_catalog.db',
    'tracker_db': '/home/lxl/src/neo_confirmation_tracker.db',
    'output_file': '/home/lxl/src/data.js',
    'days_ahead': 7,
}

# ============================================================
# NASA NEOWS Feed Fetcher
# ============================================================
def fetch_neows_feed(days_ahead=7):
    """Fetch close approaches from NASA NEOWS feed API for the next N days."""
    today = datetime.utcnow().date()
    start_date = today.isoformat()
    end_date = (today + timedelta(days=days_ahead)).isoformat()

    print(f"\n[NEOWS] Fetching feed: {start_date} → {end_date}")

    all_approaches = []
    current_start = today
    rate_limited = False
    errors = []

    while current_start < today + timedelta(days=days_ahead):
        current_end = min(current_start + timedelta(days=7), today + timedelta(days=days_ahead))
        params = {
            'start_date': current_start.isoformat(),
            'end_date': current_end.isoformat(),
            'api_key': CONFIG['nasa_api_key']
        }

        success = False
        for attempt in range(3):
            try:
                if HAS_REQUESTS:
                    resp = requests.get(CONFIG['nasa_feed_url'], params=params, timeout=30)
                    status = resp.status_code
                    if status == 200:
                        data = resp.json()
                    elif status == 429:
                        wait = 60 * (attempt + 1)
                        print(f"  [RATE LIMIT] Waiting {wait}s...")
                        rate_limited = True
                        time.sleep(wait)
                        continue
                    else:
                        errors.append(f"HTTP {status} for {current_start}→{current_end}")
                        time.sleep(5 * (attempt + 1))
                        continue
                else:
                    url = CONFIG['nasa_feed_url'] + '?' + urllib.parse.urlencode(params)
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read().decode())

                count_this_chunk = 0
                for date_str, objects in data.get('near_earth_objects', {}).items():
                    for obj in objects:
                        approaches = obj.get('close_approach_data', [])
                        if not approaches:
                            continue
                        closest = min(approaches, key=lambda a: float(a.get('miss_distance', {}).get('kilometers', float('inf'))))

                        all_approaches.append({
                            'name': obj.get('name', '').strip('()'),
                            'desig': obj.get('designation', obj.get('neo_id', '')),
                            'date': closest.get('close_approach_date', date_str),
                            'distLD': round(float(closest.get('miss_distance', {}).get('kilometers', 0)) / 400750.07, 4),
                            'vel': round(float(closest.get('relative_velocity', {}).get('kilometers_per_second', 0)), 2),
                            'dia_min': round(float(obj.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_min', 0)), 2),
                            'dia_max': round(float(obj.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max', 0)), 2),
                            'pha': obj.get('is_potentially_hazardous_asteroid', False),
                        })
                        count_this_chunk += 1

                print(f"  {current_start} → {current_end}: {count_this_chunk} objects")
                success = True
                break

            except Exception as e:
                err_msg = f"Error fetching {current_start}→{current_end} (attempt {attempt+1}): {e}"
                errors.append(err_msg)
                print(f"  {err_msg}")
                time.sleep(5 * (attempt + 1))

        if not success:
            print(f"  [WARN] Failed to fetch {current_start}→{current_end} after 3 attempts")

        current_start = current_end
        time.sleep(0.5)

    print(f"\n[NEOWS] Total close approaches fetched: {len(all_approaches)}")
    return all_approaches, rate_limited, errors


# ============================================================
# MPC NEO Confirmation Page Fetcher
# ============================================================
def fetch_mpc_candidates():
    """Fetch NEO Confirmation Page candidates from MPC."""
    print("\n[MPC] Fetching NEO Confirmation Page candidates...")
    candidates = []
    errors = []

    try:
        if HAS_REQUESTS:
            resp = requests.get("https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html",
                                timeout=30, headers={'User-Agent': 'NEO-Dashboard/1.0'})
            if resp.status_code == 200:
                candidates = parse_mpc_neocp_html(resp.text)
                print(f"[MPC] Found {len(candidates)} candidates from NEOCP")
            elif resp.status_code == 429:
                errors.append("MPC API rate limited (429)")
                print("  [RATE LIMIT] MPC API rate limited")
            else:
                errors.append(f"MPC API returned HTTP {resp.status_code}")
        else:
            req = urllib.request.Request("https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html",
                                         headers={'User-Agent': 'NEO-Dashboard/1.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                candidates = parse_mpc_neocp_html(resp.read().decode())
    except Exception as e:
        errors.append(f"Error fetching MPC NEOCP: {e}")
        print(f"  [ERROR] {e}")

    return candidates, errors


def parse_mpc_neocp_html(html):
    """Parse MPC NEO Confirmation Page HTML to extract candidates."""
    candidates = []
    try:
        from html.parser import HTMLParser

        class NEOCPParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_table = False
                self.in_row = False
                self.in_cell = False
                self.current_row = []
                self.current_cell = ""
                self.rows = []
                self.skip_header = False

            def handle_starttag(self, tag, attrs):
                if tag == 'table':
                    self.in_table = True
                    self.skip_header = True
                elif tag == 'tr' and self.in_table:
                    self.in_row = True
                    self.current_row = []
                    if self.skip_header:
                        self.skip_header = False
                        self.in_row = False
                elif tag == 'td' and self.in_row:
                    self.in_cell = True
                    self.current_cell = ""

            def handle_endtag(self, tag):
                if tag == 'td' and self.in_cell:
                    self.in_cell = False
                    self.current_row.append(self.current_cell.strip())
                elif tag == 'tr' and self.in_row:
                    self.in_row = False
                    if self.current_row:
                        self.rows.append(self.current_row)

            def handle_data(self, data):
                if self.in_cell:
                    self.current_cell += data

        parser = NEOCPParser()
        parser.feed(html)

        for row in parser.rows:
            if len(row) >= 4:
                candidate = {
                    'designation': row[0] if row else '',
                    'obs_date': row[1] if len(row) > 1 else '',
                    'ra': row[2] if len(row) > 2 else '',
                    'dec': row[3] if len(row) > 3 else '',
                    'mag': row[4] if len(row) > 4 else '',
                    'obs_count': row[5] if len(row) > 5 else '',
                    'arc_days': row[6] if len(row) > 6 else '',
                }
                if candidate['designation'] and len(candidate['designation']) >= 4:
                    candidates.append(candidate)

    except Exception as e:
        print(f"  [PARSE ERROR] {e}")

    return candidates


# ============================================================
# Database Stats Readers
# ============================================================
def read_catalog_stats():
    """Read statistics from neo_catalog.db."""
    print("\n[CATALOG] Reading local catalog stats...")
    stats = {}

    try:
        conn = sqlite3.connect(CONFIG['catalog_db'])
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM neo_catalog')
        stats['totalNEOs'] = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM neo_catalog WHERE is_pha=1')
        stats['phaCount'] = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM neo_catalog WHERE first_seen_date >= "2026-01-01"')
        stats['newThisYear'] = c.fetchone()[0]

        c.execute('SELECT COUNT(*) FROM neo_catalog WHERE first_seen_date >= "2026-06-01"')
        stats['newThisMonth'] = c.fetchone()[0]

        c.execute('SELECT orbit_class, COUNT(*) FROM neo_catalog GROUP BY orbit_class ORDER BY COUNT(*) DESC')
        orbit_classes = {}
        for row in c.fetchall():
            name = row[0] if row[0] else 'Other'
            if 'Apollo' in name:
                short = 'Apollo'
            elif 'Amor' in name:
                short = 'Amor'
            elif 'Aten' in name:
                short = 'Aten'
            elif 'Interior' in name:
                short = 'IEO'
            else:
                short = 'Other'
            orbit_classes[short] = orbit_classes.get(short, 0) + row[1]
        stats['orbitClasses'] = orbit_classes

        conn.close()
        print(f"  Total NEOs: {stats['totalNEOs']}")
        print(f"  PHAs: {stats['phaCount']}")
        print(f"  New this year: {stats['newThisYear']}")
        print(f"  New this month: {stats['newThisMonth']}")
    except Exception as e:
        print(f"  [ERROR] Failed to read catalog: {e}")
        stats = {'totalNEOs': 0, 'phaCount': 0, 'newThisYear': 0, 'newThisMonth': 0, 'orbitClasses': {}}

    return stats


def read_tracker_candidates():
    """Read candidates from neo_confirmation_tracker.db for dashboard display."""
    print("\n[TRACKER] Loading candidates from tracker DB...")
    candidates = []

    try:
        conn = sqlite3.connect(CONFIG['tracker_db'])
        c = conn.cursor()

        c.execute('''SELECT internal_id, nasa_designation, nasa_name, first_seen_date, 
                     last_seen_date, observer_code, obs_date, ra, dec, mag, 
                     obs_count, arc_days, status 
                     FROM candidate_tracking 
                     ORDER BY CAST(obs_count AS INTEGER) DESC LIMIT 20''')
        for row in c.fetchall():
            # Compute a confidence score based on obs_count and arc_days
            try:
                obs = int(row[10]) if row[10] else 0
            except (ValueError, TypeError):
                obs = 0
            try:
                arc = float(row[11]) if row[11] else 0
            except (ValueError, TypeError):
                arc = 0
            
            # Confidence heuristic: more observations + longer arc = higher confidence
            # Max ~100 obs and ~30 days arc → normalize and cap at 95
            conf = min(95, int((obs / 100.0 * 50) + (arc / 30.0 * 50)))
            
            candidates.append({
                'id': row[0] or '',  # internal_id — the MPC identifier
                'desig': row[1] or '',  # nasa_designation (may be empty for unconfirmed)
                'name': row[2] or '',  # nasa_name
                'first_seen': row[3] or '',
                'last_seen': row[4] or '',
                'observer': row[5] or '',  # observer_code
                'obs_date': row[6] or '',
                'ra': row[7] or '',
                'dec': row[8] or '',
                'mag': row[9] or '',
                'obs': obs,
                'arc': round(arc, 2),
                'confidence': conf,
                'status': row[12] or 'pending',
            })

        conn.close()
        print(f"  Loaded {len(candidates)} candidates from tracker")
    except Exception as e:
        print(f"  [ERROR] Failed to read tracker candidates: {e}")

    return candidates


def read_tracker_stats():
    """Read statistics from neo_confirmation_tracker.db."""
    print("\n[TRACKER] Reading tracker stats...")
    stats = {}

    try:
        conn = sqlite3.connect(CONFIG['tracker_db'])
        c = conn.cursor()

        c.execute('SELECT COUNT(*) FROM candidate_tracking')
        stats['totalCandidates'] = c.fetchone()[0]

        c.execute('SELECT status, COUNT(*) FROM candidate_tracking GROUP BY status')
        status_counts = {}
        for row in c.fetchall():
            status_counts[row[0]] = row[1]
        stats['statusCounts'] = status_counts

        c.execute('SELECT internal_id, status FROM candidate_tracking ORDER BY CAST(obs_count AS INTEGER) DESC LIMIT 10')
        stats['topCandidates'] = [{'designation': r[0] or 'unknown', 'status': r[1] or 'pending'} for r in c.fetchall()]

        conn.close()
        print(f"  Total candidates: {stats['totalCandidates']}")
        print(f"  Status counts: {status_counts}")
    except Exception as e:
        print(f"  [ERROR] Failed to read tracker: {e}")
        stats = {'totalCandidates': 0, 'statusCounts': {}, 'topCandidates': []}

    return stats


# ============================================================
# Data.js Generator
# ============================================================
def generate_data_js(catalog_stats, approaches, candidates, tracker_stats):
    """Generate the dashboard data.js file."""
    print("\n[OUTPUT] Generating data.js...")

    now = datetime.utcnow().isoformat()

    # Shorten orbit class names for the JS
    orbit_classes_short = catalog_stats.get('orbitClasses', {})

    # Build stats section needed by app.js
    high_conf_candidates = [c for c in candidates if c.get('confidence', 0) >= 70]
    early_discoveries = [c for c in candidates if c.get('status') == 'confirmed']
    avg_lead = sum(c.get('arc', 0) for c in candidates) / max(len(candidates), 1)

    # Compute discoveryByYear from catalog DB
    discovery_by_year = {'labels': [], 'data': []}
    try:
        cat_conn = sqlite3.connect(CONFIG['catalog_db'])
        cat_c = cat_conn.cursor()
        cat_c.execute("""SELECT SUBSTR(first_seen_date, 1, 4) as year, COUNT(*) 
                         FROM neo_catalog 
                         WHERE first_seen_date IS NOT NULL AND first_seen_date >= '2017-01-01'
                         GROUP BY year ORDER BY year""")
        for row in cat_c.fetchall():
            discovery_by_year['labels'].append(row[0])
            discovery_by_year['data'].append(row[1])
        cat_conn.close()
    except Exception as e:
        print(f"  [WARN] Could not compute discoveryByYear: {e}")

    data = {
        'meta': {
            'lastUpdate': now,
            'sources': ['NASA NEOWS', 'MPC NEO Confirmation Page', 'NASA SBDB'],
            'version': '1.0',
        },
        'catalog': {
            'totalNEOs': catalog_stats.get('totalNEOs', 0),
            'phaCount': catalog_stats.get('phaCount', 0),
            'newThisYear': catalog_stats.get('newThisYear', 0),
            'newThisMonth': catalog_stats.get('newThisMonth', 0),
            'orbitClasses': orbit_classes_short,
        },
        'approaches': approaches[:50],
        'candidates': candidates[:20],
        'tracker': {
            'totalCandidates': tracker_stats.get('totalCandidates', 0),
            'statusCounts': tracker_stats.get('statusCounts', {}),
            'topCandidates': tracker_stats.get('topCandidates', []),
        },
        'stats': {
            'earlyDiscoveries': len(early_discoveries),
            'highConfidenceCandidates': len(high_conf_candidates),
            'avgLeadTimeDays': round(avg_lead, 1),
            'discoveryByYear': discovery_by_year,
        },
        'earlyDiscoveries': early_discoveries,
    }

    js_content = f"""/**
 * NEO Dashboard — Auto-generated Data
 * Last updated: {now}
 *
 * Sources: NASA NEOWS, MPC NEO Confirmation Page, NASA SBDB
 * DO NOT EDIT — regenerated by update_dashboard.py
 */

function generateDashboardData() {{
    return {json.dumps(data, indent=4, default=str)};
}}
"""

    output_path = CONFIG['output_file']
    with open(output_path, 'w') as f:
        f.write(js_content)

    print(f"  Written to: {output_path}")
    print(f"  File size: {len(js_content)} bytes")
    return output_path


# ============================================================
# Previous Run Comparison
# ============================================================
def compare_with_previous(candidates):
    """Compare current candidates with previous run data."""
    print("\n[COMPARE] Checking for significant changes...")
    output_path = CONFIG['output_file']

    if not os.path.exists(output_path):
        print("  No previous data.js found — first run.")
        return None

    try:
        with open(output_path, 'r') as f:
            content = f.read()
        json_start = content.find('return {')
        if json_start == -1:
            print("  Could not parse previous data.js")
            return None
        # Find the matching closing brace
        depth = 0
        json_end = json_start + 7  # after "return "
        for i, ch in enumerate(content[json_end:], json_end):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break
        json_str = content[json_start + 7:json_end]
        prev_data = json.loads(json_str)
        prev_candidates = prev_data.get('candidates', [])
        print(f"  Previous candidates: {len(prev_candidates)}")
        print(f"  Current candidates: {len(candidates)}")
        return {
            'prev_count': len(prev_candidates),
            'curr_count': len(candidates),
            'change': len(candidates) - len(prev_candidates),
        }
    except Exception as e:
        print(f"  [WARN] Could not compare: {e}")
        return None


# ============================================================
# Main Pipeline
# ============================================================
def main():
    print("=" * 60)
    print("NEO Dashboard Data Update Pipeline")
    print(f"Started: {datetime.utcnow().isoformat()}")
    print("=" * 60)

    start_time = time.time()
    all_errors = []

    # 1. Fetch NEOWS feed
    approaches, rate_limited, neows_errors = fetch_neows_feed(CONFIG['days_ahead'])
    all_errors.extend(neows_errors)

    # 2. Fetch MPC NEOCP candidates
    mpc_candidates, mpc_errors = fetch_mpc_candidates()
    all_errors.extend(mpc_errors)

    # 3. Read tracker DB candidates (these are the historically tracked ones)
    tracker_candidates = read_tracker_candidates()

    # 4. Read local catalog stats
    catalog_stats = read_catalog_stats()

    # 5. Read tracker stats
    tracker_stats = read_tracker_stats()

    # 6. Merge candidates: tracker DB candidates are the main source
    all_candidates = tracker_candidates

    # 7. Compare with previous run
    comparison = compare_with_previous(all_candidates)

    # 8. Generate data.js
    output_path = generate_data_js(catalog_stats, approaches, all_candidates, tracker_stats)

    elapsed = time.time() - start_time

    # Summary Report
    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(f"Runtime: {elapsed:.1f}s")
    print(f"\nClose approaches fetched: {len(approaches)}")
    print(f"NEOCP candidates (live): {len(mpc_candidates)}")
    print(f"Tracker DB candidates:    {len(tracker_candidates)}")
    print(f"Catalog stats: {catalog_stats.get('totalNEOs', 0)} NEOs, {catalog_stats.get('phaCount', 0)} PHAs")
    print(f"Tracker: {tracker_stats.get('totalCandidates', 0)} candidates tracked")

    if comparison:
        change = comparison['change']
        if change > 0:
            print(f"\n⚠️  NEW candidates detected: +{change} since last run")
        elif change < 0:
            print(f"\nCandidates decreased: {change} since last run")
        else:
            print(f"\nNo change in candidate count since last run")

    if rate_limited:
        print("\n⚠️  NASA API rate limiting encountered (429)")

    if all_errors:
        print(f"\nErrors ({len(all_errors)}):")
        for e in all_errors:
            print(f"  - {e}")
    else:
        print("\nNo errors.")

    print(f"\nOutput: {output_path}")
    print(f"Done: {datetime.utcnow().isoformat()}")

    return {
        'approaches': len(approaches),
        'candidates': len(all_candidates),
        'errors': all_errors,
        'rate_limited': rate_limited,
        'comparison': comparison,
    }


if __name__ == '__main__':
    result = main()

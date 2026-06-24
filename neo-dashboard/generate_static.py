#!/usr/bin/env python3
"""
NEO Dashboard — Static HTML Generator
Generates a complete self-contained index.html with all data embedded.
No JavaScript dependencies — pure HTML + CSS.
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ============================================================
# Configuration
# ============================================================
CONFIG = {
    'nasa_api_key': os.environ.get('NASA_API_KEY', 'oI6kUNRErbojDSSt8Xnma6OA2UsZQAmoCOA6Tkc3'),
    'catalog_db': '/home/lxl/src/neo_catalog.db',
    'tracker_db': '/home/lxl/src/neo_confirmation_tracker.db',
    'output_dir': '/home/lxl/src/neo-dashboard',
    'days_ahead': 7,
}

# ============================================================
# Data Fetchers
# ============================================================
def fetch_approaches():
    """Fetch close approaches from NASA NEOWS feed."""
    today = datetime.utcnow().date()
    end_date = (today + timedelta(days=CONFIG['days_ahead'])).isoformat()
    
    try:
        resp = requests.get(CONFIG['nasa_feed_url'] if False else 'https://api.nasa.gov/neo/rest/v1/feed',
                           params={'start_date': today.isoformat(), 'end_date': end_date, 'api_key': CONFIG['nasa_api_key']},
                           timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            approaches = []
            for date_str, objs in data.get('near_earth_objects', {}).items():
                for obj in objs:
                    ca = obj.get('close_approach_data', [])
                    if not ca: continue
                    closest = min(ca, key=lambda a: float(a.get('miss_distance', {}).get('kilometers', float('inf'))))
                    approaches.append({
                        'name': obj.get('name', '').strip('()'),
                        'date': closest.get('close_approach_date', date_str),
                        'dist': round(float(closest.get('miss_distance', {}).get('kilometers', 0)) / 400750.07, 2),
                        'vel': round(float(closest.get('relative_velocity', {}).get('kilometers_per_second', 0)), 1),
                        'pha': obj.get('is_potentially_hazardous_asteroid', False),
                    })
            approaches.sort(key=lambda x: x['dist'])
            return approaches
    except Exception as e:
        print(f"[NEOWS] Error: {e}")
    return []

def get_catalog_stats():
    """Read catalog statistics from DB."""
    conn = sqlite3.connect(CONFIG['catalog_db'])
    c = conn.cursor()
    
    total = c.execute('SELECT COUNT(*) FROM neo_catalog').fetchone()[0]
    pha = c.execute('SELECT COUNT(*) FROM neo_catalog WHERE is_pha=1').fetchone()[0]
    new_year = c.execute('SELECT COUNT(*) FROM neo_catalog WHERE first_seen_date >= "2026-01-01"').fetchone()[0]
    new_month = c.execute('SELECT COUNT(*) FROM neo_catalog WHERE first_seen_date >= "2026-06-01"').fetchone()[0]
    
    # Orbit classes
    c.execute('SELECT orbit_class, COUNT(*) FROM neo_catalog GROUP BY orbit_class ORDER BY COUNT(*) DESC')
    orbits = {}
    for r in c.fetchall():
        name = r[0] if r[0] else 'Other'
        if 'Apollo' in name: short = 'Apollo'
        elif 'Amor' in name: short = 'Amor'
        elif 'Aten' in name: short = 'Aten'
        elif 'Interior' in name: short = 'IEO'
        else: short = 'Other'
        orbits[short] = orbits.get(short, 0) + r[1]
    
    # Discovery by year
    c.execute("""SELECT SUBSTR(first_seen_date,1,4) as yr, COUNT(*) 
                 FROM neo_catalog WHERE first_seen_date IS NOT NULL AND first_seen_date >= '2017-01-01'
                 GROUP BY yr ORDER BY yr""")
    by_year = c.fetchall()
    
    conn.close()
    return {
        'total': total, 'pha': pha, 'newYear': new_year, 'newMonth': new_month,
        'orbits': orbits, 'byYear': by_year
    }

def get_tracker_candidates():
    """Read candidates from tracker DB."""
    conn = sqlite3.connect(CONFIG['tracker_db'])
    c = conn.cursor()
    
    total = c.execute('SELECT COUNT(*) FROM candidate_tracking').fetchone()[0]
    c.execute("""SELECT internal_id, observer_code, obs_count, arc_days, mag, status 
                 FROM candidate_tracking ORDER BY CAST(obs_count AS INTEGER) DESC LIMIT 20""")
    candidates = []
    for r in c.fetchall():
        obs = int(r[2]) if r[2] else 0
        arc = float(r[3]) if r[3] else 0
        conf = min(95, int((obs / 100.0 * 50) + (arc / 30.0 * 50)))
        candidates.append({
            'id': r[0], 'observer': r[1] or '', 'obs': obs, 'arc': round(arc, 2),
            'mag': r[4] or '', 'confidence': conf, 'status': r[5] or 'pending'
        })
    
    conn.close()
    return total, candidates

# ============================================================
# HTML Generator
# ============================================================
def generate_html(stats, approaches, tracker_total, candidates, last_update):
    """Generate complete static HTML."""
    
    # Build approaches rows
    approach_rows = ''
    for a in approaches[:20]:
        pha_badge = ' <span class="pha-badge">PHA</span>' if a['pha'] else ''
        approach_rows += f'''        <tr>
            <td>{a["name"]}{pha_badge}</td>
            <td>{a["date"]}</td>
            <td>{a["dist"]} LD</td>
            <td>{a["vel"]} km/s</td>
        </tr>\n'''
    
    # Build candidates rows
    candidate_rows = ''
    for c in candidates:
        conf_color = '#3b82f6' if c['confidence'] >= 70 else '#f59e0b' if c['confidence'] >= 50 else '#64748b'
        conf_pct = c['confidence']
        candidate_rows += f'''        <tr>
            <td><code>{c["id"]}</code></td>
            <td>{c["observer"]}</td>
            <td>{c["obs"]}</td>
            <td>{c["arc"]}d</td>
            <td>{c["mag"]}</td>
            <td><span class="conf-bar"><span class="conf-fill" style="width:{conf_pct}%;background:{conf_color}"></span></span></td>
        </tr>\n'''
    
    # Build orbit bars
    orbit_bars = ''
    max_orbit = max(stats['orbits'].values()) if stats['orbits'] else 1
    orbit_colors = {'Apollo': '#3b82f6', 'Amor': '#06b6d4', 'Aten': '#8b5cf6', 'IEO': '#f59e0b', 'Other': '#64748b'}
    for name, count in sorted(stats['orbits'].items(), key=lambda x: -x[1]):
        pct = (count / stats['total']) * 100
        color = orbit_colors.get(name, '#64748b')
        orbit_bars += f'''            <div class="orbit-row">
                <span class="orbit-label">{name}</span>
                <div class="orbit-bar-bg"><div class="orbit-bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>
                <span class="orbit-count">{count:,}</span>
            </div>\n'''
    
    # Discovery by year bars
    year_bars = ''
    if stats['byYear']:
        max_year_count = max(r[1] for r in stats['byYear'])
        for year, count in stats['byYear']:
            pct = (count / max_year_count) * 100 if max_year_count > 0 else 0
            year_bars += f'''            <div class="year-row">
                <span class="year-label">{year}</span>
                <div class="year-bar-bg"><div class="year-bar-fill" style="width:{pct:.1f}%"></div></div>
                <span class="year-count">{count:,}</span>
            </div>\n'''
    
    # Stats cards
    stat_cards = f'''
        <div class="stat-card">
            <div class="stat-value">{stats['total']:,}</div>
            <div class="stat-label">Total NEOs</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{stats['pha']:,}</div>
            <div class="stat-label">PHAs</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{stats['newYear']:,}</div>
            <div class="stat-label">New This Year</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{stats['newMonth']:,}</div>
            <div class="stat-label">New This Month</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{len(approaches)}</div>
            <div class="stat-label">Approaches (7d)</div>
        </div>
        <div class="stat-card">
            <div class="stat-value">{tracker_total}</div>
            <div class="stat-label">Tracked Candidates</div>
        </div>'''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NEO Discovery Dashboard — Live Near-Earth Object Monitoring</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        :root {{
            --bg: #0a0e17;
            --card: #111827;
            --border: #1f2937;
            --text: #e5e7eb;
            --text-muted: #94a3b8;
            --accent: #3b82f6;
            --accent2: #06b6d4;
            --danger: #ef4444;
            --warning: #f59e0b;
        }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }}
        
        .header {{ background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); border-bottom: 1px solid var(--border); padding: 1.5rem 2rem; }}
        .header-inner {{ max-width: 1400px; margin: 0 auto; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 1rem; }}
        .logo {{ display: flex; align-items: center; gap: 0.75rem; }}
        .logo-icon {{ font-size: 2rem; color: var(--accent); }}
        h1 {{ font-size: 1.5rem; font-weight: 700; }}
        .subtitle {{ font-size: 0.8rem; color: var(--text-muted); }}
        
        .dashboard {{ max-width: 1400px; margin: 0 auto; padding: 1.5rem; display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }}
        .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
        .card-header {{ padding: 1rem 1.25rem; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 0.9rem; }}
        .card-body {{ padding: 1.25rem; }}
        
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 1rem; grid-column: 1 / -1; }}
        .stat-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem; text-align: center; }}
        .stat-value {{ font-size: 1.75rem; font-weight: 700; color: var(--accent2); }}
        .stat-label {{ font-size: 0.75rem; color: var(--text-muted); margin-top: 0.25rem; }}
        
        table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
        th {{ text-align: left; padding: 0.6rem 0.75rem; color: var(--text-muted); font-weight: 500; font-size: 0.7rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
        td {{ padding: 0.55rem 0.75rem; border-bottom: 1px solid rgba(31,41,55,0.5); }}
        tr:hover td {{ background: rgba(59,130,246,0.05); }}
        
        .pha-badge {{ display: inline-block; background: var(--danger); color: white; font-size: 0.6rem; padding: 0.1rem 0.4rem; border-radius: 4px; font-weight: 700; margin-left: 0.25rem; vertical-align: middle; }}
        
        .orbit-row, .year-row {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }}
        .orbit-label, .year-label {{ width: 60px; font-size: 0.8rem; color: var(--text-muted); flex-shrink: 0; }}
        .orbit-bar-bg, .year-bar-bg {{ flex: 1; height: 18px; background: var(--border); border-radius: 4px; overflow: hidden; }}
        .orbit-bar-fill {{ height: 100%; border-radius: 4px; }}
        .year-bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent2)); border-radius: 4px; }}
        .orbit-count, .year-count {{ width: 60px; text-align: right; font-size: 0.75rem; color: var(--text-muted); flex-shrink: 0; }}
        
        .conf-bar {{ display: inline-block; width: 60px; height: 6px; background: var(--border); border-radius: 3px; overflow: hidden; vertical-align: middle; }}
        .conf-fill {{ display: block; height: 100%; border-radius: 3px; }}
        
        .footer {{ text-align: center; padding: 1.5rem; color: var(--text-muted); font-size: 0.75rem; border-top: 1px solid var(--border); margin-top: 1rem; }}
        
        .update-time {{ grid-column: 1 / -1; text-align: center; color: var(--text-muted); font-size: 0.75rem; padding: 0.5rem; }}
        
        code {{ background: rgba(59,130,246,0.1); padding: 0.15rem 0.4rem; border-radius: 4px; font-size: 0.75rem; color: var(--accent2); }}
        
        .label {{ display: inline-block; background: var(--border); padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.7rem; margin-bottom: 0.5rem; color: var(--text-muted); }}
        
        @media (max-width: 768px) {{
            .dashboard {{ grid-template-columns: 1fr; }}
            .stats-grid {{ grid-template-columns: repeat(3, 1fr); }}
            .header-inner {{ flex-direction: column; }}
        }}
    </style>
</head>
<body>
    <header class="header">
        <div class="header-inner">
            <div class="logo">
                <div class="logo-icon">&#9678;</div>
                <div>
                    <h1>NEO Discovery Dashboard</h1>
                    <div class="subtitle">Near-Earth Object Monitoring &amp; Early Discovery System</div>
                </div>
            </div>
        </div>
    </header>
    
    <div class="dashboard">
        <div class="stats-grid">
            {stat_cards}
        </div>
        
        <div class="card">
            <div class="card-header">&#127760; Close Approaches (Next 7 Days)</div>
            <div class="card-body" style="max-height:400px;overflow-y:auto">
                <table>
                    <thead><tr><th>Object</th><th>Date</th><th>Distance</th><th>Velocity</th></tr></thead>
                    <tbody>
{approach_rows}                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">&#128300; MPC Confirmation Tracker (Top 20)</div>
            <div class="card-body" style="max-height:400px;overflow-y:auto">
                <table>
                    <thead><tr><th>ID</th><th>Obs</th><th>Count</th><th>Arc</th><th>Mag</th><th>Conf</th></tr></thead>
                    <tbody>
{candidate_rows}                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="card">
            <div class="card-header">&#128202; Orbit Class Distribution</div>
            <div class="card-body">
{orbit_bars}            </div>
        </div>
        
        <div class="card">
            <div class="card-header">&#128197; Discoveries by Year</div>
            <div class="card-body">
{year_bars}            </div>
        </div>
        
        <div class="update-time">
            Last updated: {last_update} &middot; Sources: NASA NEOWS feed, MPC NEO Confirmation Page, NASA SBDB
        </div>
    </div>
    
    <footer class="footer">
        NEO Discovery Dashboard &mdash; Auto-generated by update_dashboard.py &middot; 
        <a href="https://github.com/luoxiaoliangTj/neo-discovery" style="color:var(--accent)">GitHub</a>
    </footer>
</body>
</html>'''
    return html

# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("NEO Dashboard — Static HTML Generator")
    print(f"Started: {datetime.utcnow().isoformat()}")
    print("=" * 60)
    
    # 1. Fetch approaches
    print("\n[1/4] Fetching close approaches...")
    approaches = fetch_approaches()
    print(f"  Got {len(approaches)} approaches")
    
    # 2. Read catalog stats
    print("\n[2/4] Reading catalog stats...")
    stats = get_catalog_stats()
    print(f"  Total: {stats['total']}, PHAs: {stats['pha']}")
    
    # 3. Read tracker candidates
    print("\n[3/4] Reading tracker candidates...")
    tracker_total, candidates = get_tracker_candidates()
    print(f"  Tracked: {tracker_total}, Top candidates: {len(candidates)}")
    
    # 4. Generate HTML
    print("\n[4/4] Generating static HTML...")
    last_update = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    html = generate_html(stats, approaches, tracker_total, candidates, last_update)
    
    output_path = os.path.join(CONFIG['output_dir'], 'index.html')
    with open(output_path, 'w') as f:
        f.write(html)
    
    print(f"  Written to: {output_path}")
    print(f"  File size: {len(html)} bytes")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Approaches: {len(approaches)}")
    print(f"  Candidates: {len(candidates)}")
    print(f"  Catalog: {stats['total']:,} NEOs")
    print(f"  Output: {output_path}")
    print(f"  Done: {datetime.utcnow().isoformat()}")

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
NEO Dashboard — Static HTML Generator
Generates a complete self-contained index.html with all data embedded.
No JavaScript dependencies — pure HTML + CSS.

Data sources:
- NASA NEOWS Feed API (close approaches, next 7 days)
- NASA Browse API (NEO catalog stats, orbit classes, discovery years)
- MPC NEO Confirmation Page (candidate tracking + cross-reference discovery)
"""

import sqlite3
import json
import re
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
NASA_API_KEY = os.environ.get('NASA_API_KEY', 'oI6kUNRErbojDSSt8Xnma6OA2UsZQAmoCOA6Tkc3')
CATALOG_DB = '/home/lxl/src/neo_catalog.db'
TRACKER_DB = '/home/lxl/src/neo_confirmation_tracker.db'
OUTPUT_HTML = '/home/lxl/src/index.html'
MPC_NEOCPC_URL = 'https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html'

# ============================================================
# NASA Feed API — Close Approaches
# ============================================================
def fetch_approaches():
    """Fetch close approaches from NASA NEOWS feed."""
    today = datetime.utcnow().date()
    end_date = (today + timedelta(days=7)).isoformat()
    
    try:
        resp = requests.get('https://api.nasa.gov/neo/rest/v1/feed',
                           params={'start_date': today.isoformat(), 'end_date': end_date, 'api_key': NASA_API_KEY},
                           timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            approaches = []
            for date_str, objs in data.get('near_earth_objects', {}).items():
                for obj in objs:
                    ca = obj.get('close_approach_data', [])
                    if not ca:
                        continue
                    closest = min(ca, key=lambda a: float(a.get('miss_distance', {}).get('kilometers', float('inf'))))
                    approaches.append({
                        'name': obj.get('name', '').strip('()'),
                        'date': closest.get('close_approach_date', date_str),
                        'dist': round(float(closest.get('miss_distance', {}).get('kilometers', 0)) / 400750.07, 2),
                        'vel': round(float(closest.get('relative_velocity', {}).get('kilometers_per_second', 0)), 1),
                        'pha': obj.get('is_potentially_hazardous_asteroid', False),
                        'neo_id': obj.get('id', ''),
                    })
            approaches.sort(key=lambda x: x['dist'])
            return approaches
    except Exception as e:
        print(f"[NEOWS] Error: {e}")
    return []

# ============================================================
# NASA Browse API — Catalog Stats
# ============================================================
def get_catalog_stats():
    """Read catalog statistics from local DB."""
    conn = sqlite3.connect(CATALOG_DB)
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
    
    # Browse API total count (for cross-reference)
    browse_count = total
    
    conn.close()
    return {
        'total': total, 'pha': pha, 'newYear': new_year, 'newMonth': new_month,
        'orbits': orbits, 'byYear': by_year, 'browse_count': browse_count
    }

def get_browse_ids():
    """Get all neo_ids from Browse DB for cross-reference."""
    conn = sqlite3.connect(CATALOG_DB)
    c = conn.cursor()
    c.execute('SELECT neo_id FROM neo_catalog')
    ids = set(str(row[0]) for row in c.fetchall())
    conn.close()
    return ids

# ============================================================
# MPC NEO Confirmation Page — Candidate Tracker
# ============================================================
def fetch_mpc_candidates():
    """Parse MPC NEO Confirmation Page for candidate list."""
    try:
        resp = requests.get(MPC_NEOCPC_URL,
                           headers={'User-Agent': 'NEO-Dashboard/1.0'},
                           timeout=30)
        if resp.status_code != 200:
            print(f"[MPC] HTTP {resp.status_code}")
            return []
        
        html = resp.text
        # Find the table body
        tbody_start = html.find('<tbody>')
        tbody_end = html.find('</tbody>')
        if tbody_start == -1 or tbody_end == -1:
            return []
        
        tbody = html[tbody_start:tbody_end]
        
        # Parse each <tr> block
        candidates = []
        tr_blocks = tbody.split('<tr')
        
        for block in tr_blocks[1:]:  # skip first empty split
            # Extract designation
            desig_match = re.search(r'<span style="display:none">([A-Za-z0-9]{4,8})</span>', block)
            if not desig_match:
                continue
            desig = desig_match.group(1)
            
            # Extract all <td> blocks in order (handle <td align="...">)
            td_blocks = re.findall(r'<td[^>]*>(.*?)</td>', block, re.DOTALL)
            # td[0] = designation cell (with checkbox)
            # td[1] = observer code (score)
            # td[2] = discovery date "2026 06 23.5"
            # td[3] = RA
            # td[4] = Dec
            # td[5] = magnitude (V)
            # td[6] = updated date
            # td[7] = note
            # td[8] = NObs
            # td[9] = Arc days
            # td[10] = H (absolute magnitude)
            # td[11] = not seen days
            
            disc_date = ''
            if len(td_blocks) >= 3:
                # td[2] contains "  2026 06 23.5  "
                date_match = re.search(r'(\d{4} \d{2} \d{2}\.\d)', td_blocks[2])
                disc_date = date_match.group(1) if date_match else ''
            
            mag = ''
            if len(td_blocks) >= 6:
                # td[5] contains hidden span with mag value
                mag_match = re.search(r'<span style="display:none">([\d.]+)</span>', td_blocks[5])
                mag = mag_match.group(1) if mag_match else ''
            
            obs_count = ''
            if len(td_blocks) >= 9:
                obs_match = re.search(r'(\d+)', td_blocks[8].strip())
                obs_count = obs_match.group(1) if obs_match else ''
            
            arc_days = ''
            if len(td_blocks) >= 10:
                arc_match = re.search(r'([\d.]+)', td_blocks[9].strip())
                arc_days = arc_match.group(1) if arc_match else ''
            
            # Observer code from td[1]
            observer = ''
            if len(td_blocks) >= 2:
                obs_match = re.search(r'<span style="display:none">(\d{3,4})</span>', td_blocks[1])
                observer = obs_match.group(1) if obs_match else ''
            
            candidates.append({
                'desig': desig,
                'observer': observer,
                'disc_date': disc_date,
                'mag': mag,
                'obs': obs_count,
                'arc': arc_days,
            })
        
        return candidates
    except Exception as e:
        print(f"[MPC] Error: {e}")
        return []

def get_tracker_candidates():
    """Read candidates from local tracker DB."""
    conn = sqlite3.connect(TRACKER_DB)
    c = conn.cursor()
    
    total = c.execute('SELECT COUNT(*) FROM candidate_tracking').fetchone()[0]
    c.execute("""SELECT internal_id, observer_code, obs_count, arc_days, mag, status 
                 FROM candidate_tracking ORDER BY CAST(obs_count AS INTEGER) DESC""")
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
# Orbital Elements & Orbit Prediction
# ============================================================
def fetch_orbital_elements(neo_ids):
    """Fetch orbital elements from Browse DB for given neo_ids."""
    if not neo_ids:
        return {}
    conn = sqlite3.connect(CATALOG_DB)
    c = conn.cursor()
    placeholders = ','.join('?' * len(neo_ids))
    c.execute(f"""SELECT neo_id, name, semi_major_axis, eccentricity, 
                 inclination, perihelion_distance, orbit_class
                 FROM neo_catalog WHERE neo_id IN ({placeholders})""", list(neo_ids))
    result = {}
    for r in c.fetchall():
        a = r[2]  # semi_major_axis in AU
        e = r[3]  # eccentricity
        i = r[4]  # inclination in deg
        q = r[5]  # perihelion_distance in AU
        # Compute orbital period using Kepler's third law: P = 2*pi*sqrt(a^3/mu)
        # mu = GM_sun = 1.32712440018e20 m^3/s^2, 1 AU = 1.496e11 m
        # P_years = sqrt(a^3) for a in AU
        import math
        period = math.sqrt(a**3) if a else 0
        Q = a * (1 + e) if a else 0  # aphelion distance
        result[str(r[0])] = {
            'name': r[1] or '',
            'a': a, 'e': e, 'i': i, 'q': q, 'Q': Q,
            'period': period, 'orbit_class': r[6] or ''
        }
    conn.close()
    return result

def generate_orbit_svg(orbital_data, width=320, height=220):
    """Generate a static SVG orbit diagram for a candidate.
    Shows Earth orbit (circle at 1 AU) and candidate orbit (ellipse).
    """
    import math
    
    # Find scale: max semi-major axis in the data
    max_a = 1.5  # minimum: 1.5 AU to show Earth
    for o in orbital_data.values():
        if o['a'] > max_a:
            max_a = o['a']
    if max_a < 1.2:
        max_a = 1.2
    
    scale = min(width, height) * 0.38 / max_a  # pixels per AU
    cx, cy = width // 2, height // 2
    
    svg_parts = []
    
    # Background
    svg_parts.append(f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="background:#0a0e17;border-radius:8px;border:1px solid #1f2937;width:100%;height:auto;max-width:{width}px">')
    
    # Sun at center (with subtle pulse)
    svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="#fbbf24"><animate attributeName="r" values="5;6;5" dur="3s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;0.7;1" dur="3s" repeatCount="indefinite"/></circle>')
    svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="8" fill="none" stroke="#fbbf24" stroke-width="0.5" opacity="0.3"><animate attributeName="r" values="8;10;8" dur="3s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.3;0;0.3" dur="3s" repeatCount="indefinite"/></circle>')
    svg_parts.append(f'<text x="{cx}" y="{cy-12}" text-anchor="middle" fill="#fbbf24" font-size="9" font-family="sans-serif">Sun</text>')
    
    # Earth orbit (circle at 1 AU)
    earth_r = 1.0 * scale
    svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="{earth_r:.1f}" fill="none" stroke="#3b82f6" stroke-width="0.8" stroke-dasharray="4,3" opacity="0.6"><animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="360 {cx} {cy}" dur="20s" repeatCount="indefinite"/></circle>')
    svg_parts.append(f'<circle cx="{cx+earth_r:.1f}" cy="{cy}" r="3" fill="#3b82f6"><animateMotion dur="20s" repeatCount="indefinite" rotate="0"><mpath href="#earth-orbit"/></animateMotion></circle>')
    svg_parts.append(f'<path id="earth-orbit" d="M {cx+earth_r:.1f} {cy} A {earth_r:.1f} {earth_r:.1f} 0 1 1 {cx+earth_r:.1f} {cy} A {earth_r:.1f} {earth_r:.1f} 0 1 1 {cx+earth_r:.1f} {cy}" fill="none" stroke="none"/>')
    svg_parts.append(f'<text x="{cx+earth_r:.1f}" y="{cy+14}" text-anchor="middle" fill="#3b82f6" font-size="8" font-family="sans-serif">Earth</text>')
    
    # Candidate orbits
    colors = ['#ef4444', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899', '#06b6d4']
    for idx, (neo_id, o) in enumerate(orbital_data.items()):
        color = colors[idx % len(colors)]
        a = o['a']
        e = o['e']
        
        if a <= 0:
            continue
        
        # Draw ellipse: r(theta) = a(1-e^2)/(1+e*cos(theta))
        # Semi-minor axis: b = a*sqrt(1-e^2)
        b = a * math.sqrt(1 - e**2)
        rx = a * scale
        ry = b * scale
        
        # Perihelion direction (theta=0) is to the right
        # Draw ellipse centered at (cx + rx*e, cy) — focus at sun
        center_x = cx + rx * e
        center_y = cy
        
        # Generate points for the orbit path
        points = []
        for theta in range(0, 361, 3):
            rad = math.radians(theta)
            r = a * (1 - e**2) / (1 + e * math.cos(rad))
            px = cx + r * scale * math.cos(rad)
            py = cy + r * scale * math.sin(rad)
            points.append(f"{px:.1f},{py:.1f}")
        
        # Orbit path
        svg_parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="1.2" opacity="0.9"/>')
        
        # Animated asteroid dot moving along the orbit
        path_d = "M " + " L ".join(points)
        svg_parts.append(f'<circle cx="0" cy="0" r="3.5" fill="{color}"><animateMotion dur="6s" repeatCount="indefinite" rotate="0"><mpath href="#orbit-path-{neo_id}"/></animateMotion></circle>')
        svg_parts.append(f'<path id="orbit-path-{neo_id}" d="{path_d}" fill="none" stroke="none"/>')
        
        # Mark perihelion (closest to sun)
        peri_x = cx + o['q'] * scale
        peri_y = cy
        svg_parts.append(f'<circle cx="{peri_x:.1f}" cy="{peri_y:.1f}" r="3" fill="{color}"/>')
        
        # Label
        name = o['name'] if o['name'] else neo_id
        label_y = height - 8 - (len(orbital_data) - 1 - idx) * 12
        svg_parts.append(f'<text x="{cx}" y="{label_y}" text-anchor="middle" fill="{color}" font-size="8" font-family="sans-serif">{name} (a={a:.2f} AU, e={e:.2f})</text>')
    
    svg_parts.append('</svg>')
    return '\n'.join(svg_parts)

# ============================================================
# Cross-Reference: MPC candidates vs NASA Browse
# ============================================================
def cross_reference(mpc_candidates, browse_ids, tracker_desigs):
    """
    Find newly appeared candidates: on MPC NEOCP page but NOT yet in our tracker DB.

    Semantics:
    - MPC NEOCP page is updated daily with new candidate postings.
    - Our tracker DB records candidates we've already seen.
    - "New" = appeared on MPC page since we last checked the tracker.
    - This does NOT mean "discovered new NEO" — it means "new to our radar".

    Note: We cannot directly compare MPC designations vs NASA Browse neo_ids because
    they use completely different ID systems (provisional alphanumeric vs permanent numeric).
    The browse_ids parameter is kept for future use when NASA assigns permanent numbers.
    """
    new_candidates = []
    for c in mpc_candidates:
        desig = c['desig']
        # If this candidate is on MPC but NOT in our tracker DB, it's newly appeared
        if desig not in tracker_desigs:
            new_candidates.append(c)

    return new_candidates

# ============================================================
# HTML Generator
# ============================================================
def generate_html(stats, approaches, mpc_candidates, new_candidates, tracker_total, last_update, orbital_elements=None, orbit_svgs=None):
    """Generate complete static HTML."""
    if orbital_elements is None:
        orbital_elements = {}
    if orbit_svgs is None:
        orbit_svgs = []
    
    # --- Approaches rows (with orbital elements) ---
    approach_rows = ''
    for a in approaches[:20]:
        pha_badge = ' <span class="pha-badge">PHA</span>' if a['pha'] else ''
        # Add orbital elements if available
        orbit_info = ''
        neo_id = a.get('neo_id', '')
        if neo_id and neo_id in orbital_elements:
            o = orbital_elements[neo_id]
            orbit_info = f' <span style="color:var(--text-muted);font-size:0.7rem">a={o["a"]:.2f} AU, e={o["e"]:.2f}, {o["period"]:.1f}yr</span>'
        approach_rows += f'''        <tr>
            <td>{a["name"]}{pha_badge}{orbit_info}</td>
            <td>{a["date"]}</td>
            <td>{a["dist"]} LD</td>
            <td>{a["vel"]} km/s</td>
        </tr>\n'''
    
    # --- MPC Candidates rows ---
    mpc_rows = ''
    for c in mpc_candidates[:20]:
        mpc_rows += f'''        <tr>
            <td><code>{c["desig"]}</code></td>
            <td>{c["observer"]}</td>
            <td>{c["disc_date"]}</td>
            <td>{c["mag"]}</td>
            <td>{c["obs"]}</td>
            <td>{c["arc"]}d</td>
        </tr>\n'''
    
    # --- New candidates (appeared on MPC but not yet in tracker) ---
    new_rows = ''
    if new_candidates:
        for c in new_candidates[:20]:
            new_rows += f'''        <tr class="new-discovery">
            <td><code>{c["desig"]}</code></td>
            <td>{c["observer"]}</td>
            <td>{c["disc_date"]}</td>
            <td>{c["mag"]}</td>
            <td>{c["obs"]}</td>
            <td>{c["arc"]}d</td>
            <td><span class="new-badge">UNCONFIRMED</span></td>
        </tr>\n'''
    else:
        new_rows = '        <tr class="no-new"><td colspan="7">No new unconfirmed candidates this cycle — all MPC candidates already tracked</td></tr>\n'
    
    # --- Orbit bars ---
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
    
    # --- Discovery by year bars ---
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
    
    # --- Stats cards ---
    new_count = len(new_candidates)
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
        <div class="stat-card {'stat-highlight' if new_count > 0 else ''}">
            <div class="stat-value">{new_count}</div>
            <div class="stat-label">New Candidates</div>
        </div>'''
    
    # --- Orbit Prediction section ---
    orbit_section = ''
    if orbit_svgs:
        orbit_cards = ''
        for i, svg in enumerate(orbit_svgs):
            orbit_cards += f'''            <div class="orbit-card">{svg}</div>\n'''
        orbit_section = f'''
        <div class="card" style="grid-column: 1 / -1; border-left: 3px solid #f59e0b;">
            <div class="card-header">&#128640; Orbit Prediction — Top 5 Closest Approaches</div>
            <div class="card-body" style="display:flex;flex-wrap:wrap;gap:1rem;justify-content:center">
{orbit_cards}            </div>
            <div style="padding:0 1.25rem 1rem;font-size:0.7rem;color:var(--text-muted);text-align:center">
                Static orbit diagram: Sun at center, Earth orbit (dashed blue), candidate orbit (colored ellipse).
                a = semi-major axis (AU), e = eccentricity. Orbits not to scale for visibility.
            </div>
        </div>
'''

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
            --success: #10b981;
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
        .stat-highlight {{ border-color: var(--success); background: rgba(16,185,129,0.05); }}
        .stat-highlight .stat-value {{ color: var(--success); }}
        
        table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
        th {{ text-align: left; padding: 0.6rem 0.75rem; color: var(--text-muted); font-weight: 500; font-size: 0.7rem; text-transform: uppercase; border-bottom: 1px solid var(--border); }}
        td {{ padding: 0.55rem 0.75rem; border-bottom: 1px solid rgba(31,41,55,0.5); }}
        tr:hover td {{ background: rgba(59,130,246,0.05); }}
        
        .pha-badge {{ display: inline-block; background: var(--danger); color: white; font-size: 0.6rem; padding: 0.1rem 0.4rem; border-radius: 4px; font-weight: 700; margin-left: 0.25rem; vertical-align: middle; }}
        .new-badge {{ display: inline-block; background: var(--success); color: white; font-size: 0.6rem; padding: 0.1rem 0.4rem; border-radius: 4px; font-weight: 700; }}
        .new-discovery td {{ background: rgba(16,185,129,0.08); }}
        .no-new td {{ text-align: center; color: var(--text-muted); padding: 1rem; }}
        
        .orbit-row, .year-row {{ display: flex; align-items: center; gap: 0.75rem; margin-bottom: 0.5rem; }}
        .orbit-label, .year-label {{ width: 60px; font-size: 0.8rem; color: var(--text-muted); flex-shrink: 0; }}
        .orbit-bar-bg, .year-bar-bg {{ flex: 1; height: 18px; background: var(--border); border-radius: 4px; overflow: hidden; }}
        .orbit-bar-fill {{ height: 100%; border-radius: 4px; }}
        .year-bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--accent2)); border-radius: 4px; }}
        .orbit-count, .year-count {{ width: 60px; text-align: right; font-size: 0.75rem; color: var(--text-muted); flex-shrink: 0; }}
        
        .orbit-card {{ flex: 1 1 280px; max-width: 340px; padding: 0.5rem; background: rgba(17,24,39,0.5); border: 1px solid var(--border); border-radius: 8px; min-width: 0; }}
        
        .footer {{ text-align: center; padding: 1.5rem; color: var(--text-muted); font-size: 0.75rem; border-top: 1px solid var(--border); margin-top: 1rem; }}
        .update-time {{ grid-column: 1 / -1; text-align: center; color: var(--text-muted); font-size: 0.75rem; padding: 0.5rem; }}
        
        code {{ background: rgba(59,130,246,0.1); padding: 0.15rem 0.4rem; border-radius: 4px; font-size: 0.75rem; color: var(--accent2); }}
        
        .label {{ display: inline-block; background: var(--border); padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.7rem; margin-bottom: 0.5rem; color: var(--text-muted); }}
        
        .source-tag {{ display: inline-block; background: rgba(59,130,246,0.1); color: var(--accent); padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.65rem; margin-right: 0.5rem; }}
        
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
        
        # --- Orbit Prediction section (prominent position) ---
        {orbit_section}
        
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
            <div class="card-header">&#128300; MPC NEO Confirmation Page (Top 20)</div>
            <div class="card-body" style="max-height:400px;overflow-y:auto">
                <table>
                    <thead><tr><th>Designation</th><th>Obs</th><th>Disc Date</th><th>Mag</th><th>Count</th><th>Arc</th></tr></thead>
                    <tbody>
{mpc_rows}                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="card" style="grid-column: 1 / -1;">
            <div class="card-header">&#127775; Unconfirmed Candidates — New on MPC NEOCP (not yet tracked)</div>
            <div class="card-body" style="max-height:300px;overflow-y:auto">
                <table>
                    <thead><tr><th>Designation</th><th>Obs</th><th>Disc Date</th><th>Mag</th><th>Count</th><th>Arc</th><th></th></tr></thead>
                    <tbody>
{new_rows}                    </tbody>
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
            Last updated: {last_update} &middot; Sources: <span class="source-tag">NASA NEOWS Feed</span><span class="source-tag">MPC NEOCP</span><span class="source-tag">NASA SBDB</span>
        </div>
    </div>
    
    <footer class="footer">
        NEO Discovery Dashboard &mdash; Auto-generated by generate_static.py &middot; 
        <a href="https://github.com/luoxiaoliangTj/neo-discovery" style="color:var(--accent)">GitHub</a> &middot;
        Unconfirmed = newly appeared on MPC NEOCP (not yet in our tracker DB)
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
    
    # 1. Fetch close approaches
    print("\n[1/6] Fetching close approaches...")
    approaches = fetch_approaches()
    print(f"  Got {len(approaches)} approaches")
    
    # 2. Read catalog stats
    print("\n[2/6] Reading catalog stats...")
    stats = get_catalog_stats()
    print(f"  Total: {stats['total']}, PHAs: {stats['pha']}")
    
    # 3. Get Browse IDs for cross-reference
    print("\n[3/6] Loading Browse IDs for cross-reference...")
    browse_ids = get_browse_ids()
    print(f"  Browse IDs: {len(browse_ids)}")
    
    # 4. Fetch MPC NEOCP candidates
    print("\n[4/6] Fetching MPC NEO Confirmation Page...")
    mpc_candidates = fetch_mpc_candidates()
    print(f"  Got {len(mpc_candidates)} MPC candidates")
    
    # Get tracker designings for cross-reference
    tracker_total, tracker_cands = get_tracker_candidates()
    tracker_desigs = set(c['id'] for c in tracker_cands)
    
    # Cross-reference: MPC has but tracker doesn't = new
    new_candidates = cross_reference(mpc_candidates, browse_ids, tracker_desigs)
    print(f"  Unconfirmed (new on MPC, not in tracker): {len(new_candidates)}")
    if new_candidates:
        for c in new_candidates[:5]:
            print(f"    {c['desig']} — disc {c['disc_date']}, mag {c['mag']}")
    
    # 5. Fetch orbital elements for closest approaches
    print("\n[5/6] Fetching orbital elements for closest approaches...")
    approach_neo_ids = [a['neo_id'] for a in approaches[:10] if a.get('neo_id')]
    orbital_elements = fetch_orbital_elements(approach_neo_ids)
    print(f"  Got orbital elements for {len(orbital_elements)} objects")
    
    # Generate orbit diagrams for top 5 closest
    orbit_neo_ids = [a['neo_id'] for a in approaches[:5] if a.get('neo_id') and a['neo_id'] in orbital_elements]
    orbit_svgs = []
    if orbit_neo_ids:
        for oid in orbit_neo_ids:
            svg = generate_orbit_svg({oid: orbital_elements[oid]})
            orbit_svgs.append(svg)
        print(f"  Generated {len(orbit_svgs)} orbit diagrams")
    
    # 6. Generate HTML
    print("\n[6/6] Generating static HTML...")
    last_update = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    html = generate_html(stats, approaches, mpc_candidates, new_candidates, tracker_total, last_update, orbital_elements, orbit_svgs)
    
    with open(OUTPUT_HTML, 'w') as f:
        f.write(html)
    
    print(f"  Written to: {OUTPUT_HTML}")
    print(f"  File size: {len(html)} bytes")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Approaches: {len(approaches)}")
    print(f"  MPC Candidates: {len(mpc_candidates)}")
    print(f"  NEW Discoveries: {len(new_candidates)}")
    print(f"  Catalog: {stats['total']:,} NEOs")
    print(f"  Output: {OUTPUT_HTML}")
    print(f"  Done: {datetime.utcnow().isoformat()}")

if __name__ == '__main__':
    main()

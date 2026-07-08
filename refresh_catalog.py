#!/usr/bin/env python3
"""
Lightweight daily catalog refresh.
- Fetches latest orbital data for NEOs in the current 7-day feed
- Updates neo_catalog.db for just those objects (~35 NEOs vs 61,912 full)
- This keeps the dashboard "fresh" without full re-sync (which is weekly)
"""

import sqlite3, json, urllib.request, os, time
from datetime import datetime, timedelta

CATALOG_DB = '/home/lxl/src/neo_catalog.db'
NASA_API_KEY = os.environ.get('NASA_API_KEY', 'DEMO_KEY')

def fetch_neo_json(neo_id):
    """Fetch single NEO details from JPL SBDB API"""
    url = f'https://ssd-api.jpl.nasa.gov/sbdb.api?sstr={neo_id}&full-prec=true'
    req = urllib.request.Request(url, headers={'User-Agent': 'HermesAgent/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data
    except Exception as e:
        print(f'    Error fetching {neo_id}: {e}')
        return None

def update_neo_in_db(cursor, neo_id, data):
    """Update a single NEO record with latest SBDB data"""
    try:
        orbit = data.get('orbit', {})
        phys = data.get('phys_par', [])
        
        # Extract orbital elements
        a = float(orbit.get('a', 0)) if orbit.get('a') else None
        e = float(orbit.get('e', 0)) if orbit.get('e') else None
        i = float(orbit.get('i', 0)) if orbit.get('i') else None
        q = float(orbit.get('q', 0)) if orbit.get('q') else None
        # Perihelion distance
        if a and e:
            perihelion = a * (1 - e)
        else:
            perihelion = q
        
        # Orbit class
        orbit_class = orbit.get('orbit_class', {}).get('name', '')
        
        # Diameter
        diameter = None
        for p in phys:
            if p.get('name') == 'diameter':
                diameter = float(p.get('value', 0))
                break
        
        # PHA from Orbit data
        if orbit.get('not_valid', False):
            is_pha = 0
        else:
            is_pha = 1 if orbit.get('pha', False) else 0
        
        cursor.execute('''UPDATE neo_catalog SET 
            eccentricity = ?, semi_major_axis = ?, inclination = ?,
            perihelion_distance = ?, orbit_class = ?,
            diameter_max_km = ?, is_pha = ?, updated_at = ?
            WHERE neo_id = ?''',
            (e, a, i, perihelion, orbit_class, 
             diameter, is_pha, datetime.utcnow().isoformat(), neo_id))
        
        return True
    except Exception as e:
        print(f'    Error updating {neo_id}: {e}')
        return False

import urllib.request

print('=== Daily Catalog Refresh ===')
print(f'Time: {datetime.utcnow().isoformat()}')

db = sqlite3.connect(CATALOG_DB)
c = db.cursor()

# Get NEOs updated in last 7 days (active ones)
c.execute('''SELECT neo_id, name FROM neo_catalog 
    WHERE updated_at > datetime("now", "-7 days") 
    ORDER BY updated_at ASC LIMIT 40''')
neos = c.fetchall()

if not neos:
    print('No recently active NEOs to refresh')
else:
    print(f'Refreshing {len(neos)} active NEOs...')
    
    updated = 0
    for neo_id, name in neos:
        data = fetch_neo_json(neo_id)
        if data and update_neo_in_db(c, neo_id, data):
            updated += 1
            print(f'  {neo_id} {name[:25]}')
        time.sleep(0.3)  # Rate limit
    
    db.commit()
    print(f'\nUpdated {updated}/{len(neos)} records')
    
    # Verify
    c.execute('SELECT MIN(updated_at), MAX(updated_at) FROM neo_catalog')
    row = c.fetchone()
    print(f'New update range: {row[0]} -> {row[1]}')

db.close()

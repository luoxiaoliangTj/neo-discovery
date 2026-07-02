#!/usr/bin/env python3
"""NEO incremental sync — finds newly appeared NEOs via API count diff."""
import requests, sqlite3, sys, time
from datetime import datetime

NASA_KEY = 'oI6kUNRErbojDSSt8Xnma6OA2UsZQAmoCOA6Tkc3'
DB_PATH = '/home/lxl/src/neo_catalog.db'
PAGE_SIZE = 20

def main():
    print("=" * 60)
    print("NEO Incremental Sync")
    print(f"Start: {datetime.utcnow().isoformat()}")
    print("=" * 60)

    # 1. Get API total
    resp = requests.get('https://api.nasa.gov/neo/rest/v1/neo/browse',
        params={'api_key': NASA_KEY, 'page': 0, 'size': 1}, timeout=30)
    api_total = int(resp.json().get('page', {}).get('total_elements', 0))
    print(f"API total: {api_total}")

    # 2. Get DB current state
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT neo_id FROM neo_catalog')
    db_ids = set(str(r[0]) for r in c.fetchall())
    existing_db_size = len(db_ids)
    print(f"API total: {api_total}")
    print(f"DB total: {existing_db_size}")

    # PATCHED 2026-07-02: 移除 count-based skip
    # 如果 API total <= DB total，无法判断是否有新天体被 NASA 加入（可能 total_elements 未变化但 ID 重排）
    # 正确的做法是去源站比对第一页 ID，而非跳过
    if api_total <= existing_db_size:
        print(f"API total ({api_total}) <= DB ({existing_db_size})，但仍继续遍历已确认没有遗漏")
    else:
        expected_new = api_total - existing_db_size
        print(f"Expected new: {expected_new}")

    # 3. Full scan, only INSERT what's missing
    inserted = 0
    errors = []
    page = 0
    while True:
        try:
            resp = requests.get('https://api.nasa.gov/neo/rest/v1/neo/browse',
                params={'api_key': NASA_KEY, 'page': page, 'size': PAGE_SIZE}, timeout=30)
            items = resp.json().get('near_earth_objects', [])
            if not items:
                break

            page_new = 0
            for item in items:
                aid = str(item.get('id', ''))
                if aid in db_ids:
                    continue
                name = item.get('name', '')
                c.execute('SELECT neo_id FROM neo_catalog WHERE name LIKE ? OR neo_id = ?',
                          (f'%{name[:20]}%', aid))
                exists = c.fetchone() is not None
                if exists:
                    # Update existing record
                    orbital = item.get('orbital_data', {})
                    c.execute('''UPDATE neo_catalog SET
                        name=?, designation=?, is_pha=?, diameter_min_km=?, diameter_max_km=?,
                        eccentricity=?, semi_major_axis=?, perihelion_distance=?, inclination=?,
                        orbit_class=?, first_seen_date=?, last_seen_date=?, nasa_url=?, updated_at=?
                        WHERE neo_id=?''',
                        (name, item.get('designation'),
                         1 if item.get('is_potentially_hazardous_asteroid') else 0,
                         item.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_min'),
                         item.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max'),
                         orbital.get('eccentricity'), orbital.get('semi_major_axis'),
                         orbital.get('perihelion_distance'), orbital.get('inclination'),
                         orbital.get('orbit_class', {}).get('orbit_class_description', ''),
                         orbital.get('first_observation_date'), orbital.get('last_observation_date'),
                         item.get('nasa_jpl_url'), datetime.utcnow().isoformat(), aid))
                    inserted += 1
                else:
                    # Brand new — insert
                    orbital = item.get('orbital_data', {})
                    c.execute('''INSERT INTO neo_catalog
                        (neo_id, name, designation, is_pha, diameter_min_km, diameter_max_km,
                         eccentricity, semi_major_axis, perihelion_distance, inclination,
                         orbit_class, first_seen_date, last_seen_date, nasa_url, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (aid, name, item.get('designation'),
                         1 if item.get('is_potentially_hazardous_asteroid') else 0,
                         item.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_min'),
                         item.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max'),
                         orbital.get('eccentricity'), orbital.get('semi_major_axis'),
                         orbital.get('perihelion_distance'), orbital.get('inclination'),
                         orbital.get('orbit_class', {}).get('orbit_class_description', ''),
                         orbital.get('first_observation_date'), orbital.get('last_observation_date'),
                         item.get('nasa_jpl_url'), datetime.utcnow().isoformat()))
                    db_ids.add(aid)
                    page_new += 1

            conn.commit()
            page += 1
            if page % 100 == 0:
                print(f"  Page {page}: +{page_new} 新 / {len(db_ids)} 总")
            time.sleep(1.5)
        except Exception as e:
            print(f"  Error: {e}")
            time.sleep(5)

    final = len(db_ids)
    print(f"\nDone: {inserted} updates, DB now {final}")
    conn.close()
    return inserted

if __name__ == '__main__':
    sys.exit(main())

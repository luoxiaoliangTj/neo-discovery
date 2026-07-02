#!/usr/bin/env python3
"""
NEO Daily Discovery Pipeline v4
================================
设计原则（用户严格要求）：
1. 去源站获取真实数据，不做本地自检跳过
2. 增量更新：只同步缺失天体，不重复全量
3. 同步后验证：确认新ID已入库才算成功
4. 永不短路：任何 count-match 都不能作为跳过检测的理由

流程：
  Feed API（未来7天）→ cross-match（增量发现）→ 定向拉取（仅缺失ID）→
  验证 → 记录 → 报告 → 推送

每日耗时目标：<30秒（Feed API 3次 + 定向fetch <5个 × 2秒）

全量同步（每周）：单独的 full_browse_sync 函数，由每周一 cron 调用。
"""

import requests
import sqlite3
import time
import os
import sys
import json
import subprocess
from datetime import datetime, timedelta

# ============================================================
# 配置
# ============================================================
CONFIG = {
    'nasa_api_key': os.environ.get('NASA_API_KEY', 'oI6kUNRErbojDSSt8Xnma6OA2UsZQAmoCOA6Tkc3'),
    'nasa_browse_url': 'https://api.nasa.gov/neo/rest/v1/neo/browse',
    'nasa_feed_url': 'https://api.nasa.gov/neo/rest/v1/feed',
    'check_days': 7,
    'db_path': '/home/lxl/src/neo_catalog.db',
    'output_dir': '/home/lxl/src/output',
    'report_path': '/home/lxl/src/output/neo_discovery_report.html',
    'page_size': 20,
    'rate_limit_pause': 2.0,
}

THRESHOLDS = {
    'close_very_km': 500_000,
    'close_km': 1_000_000,
    'large_big_km': 0.5,
    'large_km': 0.14,
    'high_vel': 20,
    'high_ecc': 0.6,
    'pha_bonus': 3,
}

# ============================================================
# 数据库
# ============================================================
class NeoDatabase:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS neo_catalog (
                neo_id TEXT PRIMARY KEY,
                name TEXT,
                designation TEXT,
                is_pha INTEGER,
                diameter_min_km REAL,
                diameter_max_km REAL,
                eccentricity REAL,
                semi_major_axis REAL,
                perihelion_distance REAL,
                inclination REAL,
                orbit_class TEXT,
                first_seen_date TEXT,
                last_seen_date TEXT,
                nasa_url TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS discoveries (
                discovery_id TEXT PRIMARY KEY,
                neo_id TEXT,
                name TEXT,
                designation TEXT,
                discovery_date TEXT,
                miss_distance_km REAL,
                velocity_km_s REAL,
                diameter_km REAL,
                is_pha INTEGER,
                threat_score INTEGER,
                threat_level TEXT,
                reason TEXT,
                first_approach_date TEXT,
                FOREIGN KEY (neo_id) REFERENCES neo_catalog(neo_id)
            );
            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT,
                scan_type TEXT,
                neows_count INTEGER,
                catalog_count INTEGER,
                new_count INTEGER,
                status TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_designation ON neo_catalog(designation);
            CREATE INDEX IF NOT EXISTS idx_name ON neo_catalog(name);
            CREATE INDEX IF NOT EXISTS idx_discovery_date ON discoveries(discovery_date);
        """)
        self.conn.commit()

    def get_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM neo_catalog").fetchone()[0]

    def neo_exists(self, neo_id):
        return self.conn.execute("SELECT 1 FROM neo_catalog WHERE neo_id = ?", (str(neo_id),)).fetchone() is not None

    def get_all_neo_ids(self):
        return {str(r[0]) for r in self.conn.execute("SELECT neo_id FROM neo_catalog").fetchall()}

    def get_known_designations(self):
        return {r[0] for r in self.conn.execute("SELECT designation FROM neo_catalog WHERE designation IS NOT NULL").fetchall()}

    def get_known_names(self):
        return {r[0] for r in self.conn.execute("SELECT name FROM neo_catalog WHERE name IS NOT NULL").fetchall()}

    def upsert_neo(self, neo_data):
        self.conn.execute("""
            INSERT OR REPLACE INTO neo_catalog 
            (neo_id, name, designation, is_pha, diameter_min_km, diameter_max_km,
             eccentricity, semi_major_axis, perihelion_distance, inclination,
             orbit_class, first_seen_date, last_seen_date, nasa_url, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            neo_data['neo_id'],
            neo_data.get('name'),
            neo_data.get('designation'),
            neo_data.get('is_pha', 0),
            neo_data.get('diameter_min_km'),
            neo_data.get('diameter_max_km'),
            neo_data.get('eccentricity'),
            neo_data.get('semi_major_axis'),
            neo_data.get('perihelion_distance'),
            neo_data.get('inclination'),
            neo_data.get('orbit_class'),
            neo_data.get('first_seen_date'),
            neo_data.get('last_seen_date'),
            neo_data.get('nasa_url'),
            datetime.utcnow().isoformat()
        ))

    def insert_discovery(self, d):
        self.conn.execute("""
            INSERT OR REPLACE INTO discoveries
            (discovery_id, neo_id, name, designation, discovery_date,
             miss_distance_km, velocity_km_s, diameter_km, is_pha,
             threat_score, threat_level, reason, first_approach_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d['discovery_id'], d['neo_id'], d.get('name',''), d.get('designation',''),
            d['discovery_date'], d.get('miss_distance_km'), d.get('velocity_km_s'),
            d.get('diameter_km'), d.get('is_pha', False),
            d.get('threat_score', 0), d.get('threat_level', 'low'),
            d.get('reason',''), d.get('first_approach_date','')
        ))

    def log_scan(self, scan_type, source_count, catalog_count, new_count, status):
        self.conn.execute("""
            INSERT INTO scan_log (scan_time, scan_type, neows_count, catalog_count, new_count, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), scan_type, source_count, catalog_count, new_count, status))

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


# ============================================================
# NASA API
# ============================================================
def fetch_browse_page(page, page_size):
    for attempt in range(3):
        try:
            resp = requests.get(CONFIG['nasa_browse_url'],
                params={'api_key': CONFIG['nasa_api_key'], 'page': page, 'size': page_size}, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('near_earth_objects', []), data.get('page', {}).get('total_elements', '?')
            if resp.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            if attempt < 2:
                time.sleep(5)
        except requests.exceptions.Timeout:
            time.sleep(5)
        except Exception:
            time.sleep(3)
    return [], '?'


def fetch_neo_by_id(neo_id):
    """定向获取单个NEO详情"""
    url = f"{CONFIG['nasa_browse_url'].rsplit('/', 1)[0]}/neo/{neo_id}"
    for attempt in range(3):
        try:
            resp = requests.get(url, params={'api_key': CONFIG['nasa_api_key']}, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                time.sleep(60 * (attempt + 1))
                continue
            if attempt < 2:
                time.sleep(3)
        except Exception:
            time.sleep(3)
    return None


def fetch_feed_approaches(days_ahead):
    """获取未来N天的NEOWS feed（每次最多7天）"""
    today = datetime.utcnow().date()
    all_approaches = []
    current_start = today

    while current_start < today + timedelta(days=days_ahead):
        current_end = min(current_start + timedelta(days=7), today + timedelta(days=days_ahead))
        params = {
            'start_date': current_start.isoformat(),
            'end_date': current_end.isoformat(),
            'api_key': CONFIG['nasa_api_key']
        }
        for attempt in range(3):
            try:
                resp = requests.get(CONFIG['nasa_feed_url'], params=params, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    day_count = 0
                    for date_str, objects in data.get('near_earth_objects', {}).items():
                        day_count += len(objects)
                        for obj in objects:
                            approaches = obj.get('close_approach_data', [])
                            if not approaches:
                                continue
                            closest = min(approaches, key=lambda a: float(a.get('miss_distance', {}).get('kilometers', float('inf'))))
                            all_approaches.append({
                                'neo_id': str(obj.get('id', '')),
                                'name': obj.get('name', ''),
                                'designation': obj.get('name_limited', obj.get('designation', '')),
                                'date': closest.get('close_approach_date', date_str),
                                'miss_distance_km': float(closest.get('miss_distance', {}).get('kilometers', 0)),
                                'velocity_km_s': float(closest.get('relative_velocity', {}).get('kilometers_per_second', 0)),
                                'diameter_min_km': float(obj.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_min', 0)),
                                'diameter_max_km': float(obj.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max', 0)),
                                'is_pha': obj.get('is_potentially_hazardous_asteroid', False),
                                'nasa_url': obj.get('nasa_jpl_url', ''),
                            })
                    print(f"  Feed {current_start} → {current_end}: {day_count}")
                    break
                if resp.status_code == 429:
                    time.sleep(60 * (attempt + 1))
                    continue
                if attempt < 2:
                    time.sleep(5)
            except requests.exceptions.Timeout:
                time.sleep(5)
            except Exception as e:
                print(f"  Feed error: {e}")
                time.sleep(3)
        current_start = current_end

    return all_approaches


def parse_browse_item(neo):
    try:
        orbital = neo.get('orbital_data', {})
        return {
            'neo_id': str(neo.get('id', '')),
            'name': neo.get('name'),
            'designation': neo.get('designation'),
            'is_pha': 1 if neo.get('is_potentially_hazardous_asteroid') else 0,
            'diameter_min_km': neo.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_min'),
            'diameter_max_km': neo.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max'),
            'eccentricity': orbital.get('eccentricity'),
            'semi_major_axis': orbital.get('semi_major_axis'),
            'perihelion_distance': orbital.get('perihelion_distance'),
            'inclination': orbital.get('inclination'),
            'orbit_class': orbital.get('orbit_class', {}).get('orbit_class_description', ''),
            'first_seen_date': orbital.get('first_observation_date'),
            'last_seen_date': orbital.get('last_observation_date'),
            'nasa_url': neo.get('nasa_jpl_url'),
        }
    except Exception as e:
        print(f"  parse error: {e}")
        return None


# ============================================================
# 核心发现逻辑
# ============================================================
def cross_match(db, feed_approaches):
    known_ids = db.get_all_neo_ids()
    known_names = db.get_known_names()
    known_designations = db.get_known_designations()

    new_candidates = []
    known_approaches = []

    for obj in feed_approaches:
        neo_id = obj.get('neo_id', '')
        name = obj.get('name', '')
        is_new = False
        reason = ''

        if neo_id and neo_id not in known_ids:
            is_new = True
            reason += f"neo_id {neo_id} 不在本地星表中; "
        if name and name not in known_names and name not in known_designations:
            if is_new:
                reason += f"名称 {name} 也不在星表中; "

        if is_new:
            new_candidates.append({**obj, 'is_new': True, 'reason': reason.strip('; ')})
        else:
            known_approaches.append(obj)

    return new_candidates, known_approaches


def verify_candidate(candidate):
    """通过NEO详情API验证新发现候选体"""
    neo_id = candidate.get('neo_id', '')
    name = candidate.get('name', '')
    confidence = 'low'
    details = []

    detail = fetch_neo_by_id(neo_id)
    if not detail:
        return True, 'low', 'NEOWS详情获取失败（可能是数据未就绪）'

    od = detail.get('orbital_data', {})

    # 轨道合理性检查
    ecc = float(od.get('eccentricity', 0))
    a = float(od.get('semi_major_axis', 0))
    q = float(od.get('perihelion_distance', 0))

    if ecc > 0.95 or a > 15 or (q and q < 0.01):
        return False, 'low', f"轨道异常: e={ecc:.3f}, a={a:.2f}AU"

    # 数据质量
    orbit_uncertainty = od.get('orbit_uncertainty', '9')
    data_arc = od.get('data_arc_in_days', 0)
    obs_used = detail.get('observations_used', 0)
    details.append(f"U={orbit_uncertainty}, {data_arc}天, {obs_used}测")

    if orbit_uncertainty in ('0', '1', '2') and data_arc > 7 and obs_used > 20:
        confidence = 'high'
    elif orbit_uncertainty in ('3', '4', '5') and data_arc > 3:
        confidence = 'medium'
    else:
        confidence = 'low'

    # 检查是否已有天体换名
    conn = sqlite3.connect(CONFIG['db_path'])
    match = conn.execute(
        "SELECT neo_id FROM neo_catalog WHERE name = ? OR designation = ?", (name, name)
    ).fetchone()
    conn.close()

    if match:
        return False, 'low', f"名称匹配已知天体 {match[0]}"

    details.append(f"a={a:.2f}AU e={ecc:.3f} q={q:.3f}AU")
    return True, confidence, '; '.join(details)


def fetch_missing_neos(db, missing_ids):
    """定向从NASA拉取缺失的NEO详情并入库"""
    inserted = 0
    errors = []
    for neo_id in missing_ids:
        detail = fetch_neo_by_id(neo_id)
        if not detail:
            errors.append(neo_id)
            continue
        neo_data = parse_browse_item(detail)
        if neo_data:
            db.upsert_neo(neo_data)
            inserted += 1
        time.sleep(CONFIG['rate_limit_pause'])
    db.commit()
    return inserted, errors


# ============================================================
# 全量同步（仅周调用）
# ============================================================
def full_browse_sync(db):
    """全量同步NEO星表—仅通过增量方式（拉缺失ID）
    流程：
    1. 读取本地已有ID
    2. 遍历browse分页（有界）
    3. 每遇到不在库中的ID→定向fetch详情→insert
    4. 连续N页无新记录时退出
    """
    existing_ids = db.get_all_neo_ids()
    print(f"  Local catalog: {len(existing_ids)} objects")

    items, api_total = fetch_browse_page(0, CONFIG['page_size'])
    if api_total == '?':
        api_total = 0
    print(f"  NASA api_total: {api_total}")

    if api_total < len(existing_ids):
        print(f"  API total ({api_total}) < local ({len(existing_ids)}) — possible reclassification, re-scanning")

    total_pages = max((api_total + 19) // 20, 100)
    inserted = 0
    errors = []
    consecutive_empty = 0
    EMPTY_EXIT_THRESHOLD = 100  # 连续100页无新→退出

    for page in range(total_pages):
        if page == 0:
            page_items = items
        else:
            page_items, _ = fetch_browse_page(page, CONFIG['page_size'])
            time.sleep(CONFIG['rate_limit_pause'])

        if not page_items:
            consecutive_empty += 1
            if consecutive_empty >= EMPTY_EXIT_THRESHOLD:
                print(f"  Exiting after {EMPTY_EXIT_THRESHOLD} empty pages (page {page})")
                break
            continue

        page_new_ids = []
        for item in page_items:
            aid = str(item.get('id', ''))
            if aid not in existing_ids:
                page_new_ids.append(aid)

        if page_new_ids:
            consecutive_empty = 0
            print(f"  Page {page}: {len(page_new_ids)} new IDs → fetching...")
            for nid in page_new_ids:
                detail = fetch_neo_by_id(nid)
                if detail:
                    nd = parse_browse_item(detail)
                    if nd:
                        db.upsert_neo(nd)
                        existing_ids.add(nid)
                        inserted += 1
                time.sleep(CONFIG['rate_limit_pause'])
            db.commit()
        else:
            consecutive_empty += 1
            if consecutive_empty >= EMPTY_EXIT_THRESHOLD:
                print(f"  Exiting: {EMPTY_EXIT_THRESHOLD} consecutive known pages at page {page}")
                break

    db.commit()
    print(f"  Full sync done: {inserted} new objects inserted")
    return inserted


# ============================================================
# 威胁评分
# ============================================================
def score_threat(obj):
    score = 0
    reasons = []
    if obj.get('is_pha'):
        score += THRESHOLDS['pha_bonus']
        reasons.append('PHA')
    dist = obj.get('miss_distance_km', float('inf'))
    if dist < THRESHOLDS['close_very_km']:
        score += 3; reasons.append(f'极近({dist/1000:.0f}万km)')
    elif dist < THRESHOLDS['close_km']:
        score += 1; reasons.append(f'近({dist/1000:.0f}万km)')
    diam = obj.get('diameter_max_km', 0) or obj.get('diameter_min_km', 0) or 0
    if diam >= THRESHOLDS['large_big_km']:
        score += 2; reasons.append(f'大型({diam:.2f}km)')
    elif diam >= THRESHOLDS['large_km']:
        score += 1; reasons.append(f'中型({diam:.2f}km)')
    vel = obj.get('velocity_km_s', 0)
    if vel > THRESHOLDS['high_vel']:
        score += 1; reasons.append(f'高速({vel:.1f}km/s)')
    ecc = obj.get('eccentricity')
    if ecc and ecc > THRESHOLDS['high_ecc']:
        score += 1; reasons.append(f'高偏心率({ecc:.2f})')
    level = 'critical' if score >= 7 else ('high' if score >= 5 else ('medium' if score >= 3 else 'low'))
    return min(score, 10), level, reasons


# ============================================================
# 报告生成
# ============================================================
def _table_row(c, is_rejected=False):
    name = c.get('name') or c.get('designation') or c.get('neo_id', '?')
    pha = 'PHA' if c.get('is_pha') else '--'
    dist = f"{c.get('miss_distance_km', 0)/10000:.1f}万km" if c.get('miss_distance_km') else '?'
    vel = f"{c.get('velocity_km_s', 0):.1f}km/s" if c.get('velocity_km_s') else '?'
    diam = f"{c.get('diameter_max_km', 0):.3f}km" if c.get('diameter_max_km') else '?'
    badge = c.get('threat_level', 'low')
    score = c.get('threat_score', 0)
    details = c.get('verify_details', '')
    neo_id = c.get('neo_id', '?')
    date = c.get('date', '?')
    if is_rejected:
        return f'<tr><td class="name">{name}</td><td>{neo_id}</td><td class="reason">{details}</td></tr>'
    else:
        return (f'<tr><td class="name">{name}</td><td>{date}</td><td>{dist}</td>'
                f'<td>{vel}</td><td>{diam}</td><td>{pha}</td>'
                f'<td><span class="badge {badge}">{score}/10</span></td>'
                f'<td class="{badge}">{badge}</td>'
                f'<td class="reason">{details}</td></tr>')


def generate_report(confirmed, uncertain, rejected, known_approaches, db_stats):
    now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    scored_confirmed = []
    for obj in confirmed:
        score, level, reasons = score_threat(obj)
        scored_confirmed.append({**obj, 'threat_score': score, 'threat_level': level, 'verify_details': reasons})
    scored_confirmed.sort(key=lambda x: x['threat_score'], reverse=True)

    scored_uncertain = []
    for obj in uncertain:
        score, level, reasons = score_threat(obj)
        scored_uncertain.append({**obj, 'threat_score': score, 'threat_level': level, 'verify_details': reasons})
    scored_uncertain.sort(key=lambda x: x['threat_score'], reverse=True)

    total = len(scored_confirmed) + len(scored_uncertain) + len(rejected) + len(known_approaches)

    css = """*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#c9d1d9;padding:20px;max-width:1200px;margin:0 auto;}
h1{color:#58a6ff;margin-bottom:10px;}
h2{color:#f0883e;margin:20px 0 10px;}
.stats{display:flex;gap:15px;margin:15px 0;flex-wrap:wrap;}
.stat{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 20px;text-align:center;}
.stat h3{font-size:2em;margin-bottom:4px;}
.stat p{color:#8b949e;font-size:0.85em;}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:0.85em;}
th{background:#161b22;color:#58a6ff;padding:8px 6px;text-align:left;border-bottom:2px solid #30363d;}
td{padding:7px 6px;border-bottom:1px solid #21262d;}
tr:hover{background:#161b22;}
.name{font-weight:bold;}
.critical{color:#f85149;}.high{color:#f0883e;}.medium{color:#d29922;}.low{color:#3fb950;}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:0.75em;font-weight:bold;}
.badge.critical{background:#f8514920;color:#f85149;}
.badge.high{background:#f0883e20;color:#f0883e;}
.badge.medium{background:#d2992220;color:#d29922;}
.badge.low{background:#3fb95020;color:#3fb950;}
.reason{color:#8b949e;font-size:0.8em;}
.footer{margin-top:30px;color:#484f58;font-size:0.8em;border-top:1px solid #21262d;padding-top:10px;}"""

    html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8"><style>{css}</style></head>
<body>
<h1>NEO 候选体发现报告</h1>
<p style="color:#8b949e">生成时间: {now} | 星表: {db_stats['catalog_count']} 个 | 本轮: {total} 个事件</p>

<div class="stats">
  <div class="stat"><h3 style="color:#3fb950">{len(scored_confirmed)}</h3><p>确认发现</p></div>
  <div class="stat"><h3 style="color:#d29922">{len(scored_uncertain)}</h3><p>待观察</p></div>
  <div class="stat"><h3 style="color:#8b949e">{len(rejected)}</h3><p>误报</p></div>
  <div class="stat"><h3 style="color:#f85149">{sum(1 for c in scored_confirmed if c["threat_level"]=="critical")}</h3><p>极高威胁</p></div>
  <div class="stat"><h3 style="color:#f0883e">{sum(1 for c in scored_confirmed if c["threat_level"]=="high")}</h3><p>高威胁</p></div>
</div>
'''

    if scored_confirmed:
        html += '<table><tr><th>名称</th><th>最近接近</th><th>距离</th><th>速度</th><th>直径</th><th>PHA</th><th>威胁</th><th>评级</th><th>验证详情</th></tr>'
        for c in scored_confirmed:
            html += _table_row(c)
        html += '</table>'

    if scored_uncertain:
        html += '<table><tr><th>名称</th><th>最近接近</th><th>距离</th><th>速度</th><th>直径</th><th>PHA</th><th>威胁</th><th>评级</th><th>原因</th></tr>'
        for c in scored_uncertain:
            html += _table_row(c)
        html += '</table>'

    if rejected:
        html += f'<h2>误报（{len(rejected)} 个）</h2>'
        html += '<table><tr><th>名称</th><th>neo_id</th><th>原因</th></tr>'
        for c in rejected:
            html += _table_row(c, is_rejected=True)
        html += '</table>'

    if not scored_confirmed and not scored_uncertain and not rejected:
        html += '<h2>本轮无新发现</h2><p style="color:#8b949e">所有 feed 中的天体都在星表中。</p>'

    high_threat_known = [a for a in known_approaches if a.get('miss_distance_km', float('inf')) < THRESHOLDS['close_km'] or a.get('is_pha')]
    if high_threat_known:
        html += '<h2>已知天体近距离提醒</h2>'
        html += '<table><tr><th>名称</th><th>距离</th><th>速度</th><th>PHA</th></tr>'
        for obj in sorted(high_threat_known, key=lambda x: x.get('miss_distance_km', float('inf')))[:10]:
            name = obj.get('name') or obj.get('designation') or '?'
            dist = f"{obj.get('miss_distance_km', 0)/10000:.1f}万km"
            vel = f"{obj.get('velocity_km_s', 0):.1f}km/s"
            pha = 'PHA' if obj.get('is_pha') else '--'
            html += f'<tr><td>{name}</td><td>{dist}</td><td>{vel}</td><td>{pha}</td></tr>'
        html += '</table>'

    html += f'''
<div class="footer">
NEO Discovery Pipeline v4 | 星表: {db_stats['catalog_count']} 个<br>
确认发现 = 轨道合理性 + 数据质量 + 非编号变更。不直接公布，仅供内部筛查参考。
</div></body></html>'''

    os.makedirs(CONFIG['output_dir'], exist_ok=True)
    with open(CONFIG['report_path'], 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n报告已保存: {CONFIG['report_path']}")
    return CONFIG['report_path']


# ============================================================
# 推送
# ============================================================
def send_to_feishu(report_path, summary):
    feishu_target = "feishu:oc_81d6aefbf14776f2a97551ec43179806"
    new_count = summary.get('confirmed_count', 0) + summary.get('uncertain_count', 0)
    catalog_count = summary.get('catalog_count', 0)

    if new_count > 0:
        msg = f"NEO 候选体发现报告 v4\n\n星表: {catalog_count} 个\n新发现: {new_count} 个候选体\n\n报告文件已附后"
    else:
        msg = f"NEO 每日报告 v4\n\n星表: {catalog_count} 个\n本轮无新发现"

    try:
        result = subprocess.run(
            ['hermes', 'send', '--to', feishu_target, msg],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print("飞书摘要已发送")
        else:
            print(f"飞书发送失败: {result.stderr[:200]}")

        if new_count > 0:
            result2 = subprocess.run(
                ['hermes', 'send', '--to', feishu_target, f'MEDIA:{report_path}'],
                capture_output=True, text=True, timeout=30
            )
            if result2.returncode == 0:
                print("飞书报告文件已发送")
    except Exception as e:
        print(f"飞书发送异常: {e}")


# ============================================================
# 主流程 — DUPLICATE: see def main() below. This was the
# erroneously duplicated block.
# ============================================================

# !!! The following duplicate main() block was a bug·
# The real main() is defined below at the file's end.
# def main(): ...    <-- this is the duplicate / redundant copy
# ============================================================


# ============================================================
# 主流程 (real main)
# ============================================================
def main():
    """Daily discovery pipeline"""
    print("=" * 60)
    print("NEO Discovery Pipeline v4 (Daily)")
    print(f"Time: {datetime.utcnow().isoformat()}")
    print("=" * 60)

    db = NeoDatabase(CONFIG['db_path'])
    db_count_before = db.get_count()
    print(f"Local catalog: {db_count_before} objects")

    # STEP 1: Fetch NEOWS feed (future 7 days)
    print("\n### STEP 1: Fetch NEOWS feed")
    feed_approaches = fetch_feed_approaches(CONFIG['check_days'])
    print(f"  Feed total: {len(feed_approaches)} approach events")

    # STEP 2: Cross-match
    print("\n### STEP 2: Cross-match vs local catalog")
    new_candidates, known_approaches = cross_match(db, feed_approaches)
    print(f"  New candidates: {len(new_candidates)}")
    print(f"  Known: {len(known_approaches)}")

    # STEP 3: Verify + fetch + upsert missing
    confirmed = []
    uncertain = []
    rejected = []
    fetch_errors = []

    if new_candidates:
        print(f"\n### STEP 3: Verify {len(new_candidates)} candidates")
        for cand in new_candidates:
            is_valid, confidence, details = verify_candidate(cand)
            print(f"  {cand.get('neo_id')}: valid={is_valid} conf={confidence} — {details[:80]}")

            if not is_valid:
                rejected.append({**cand, 'confidence': 'rejected', 'verify_details': details})
                continue

            # Fetch full details and insert to catalog
            detail = fetch_neo_by_id(cand['neo_id'])
            if detail:
                nd = parse_browse_item(detail)
                if nd:
                    db.upsert_neo(nd)
                    print(f"    → inserted into catalog: {nd.get('name')}")
                else:
                    fetch_errors.append(cand['neo_id'])
            else:
                fetch_errors.append(cand['neo_id'])

            if confidence in ('high', 'medium'):
                confirmed.append({**cand, 'confidence': confidence, 'verify_details': details})
            else:
                uncertain.append({**cand, 'confidence': 'low', 'verify_details': details})

            time.sleep(CONFIG['rate_limit_pause'])

        db.commit()

    # STEP 4: Verification — confirm all new IDs now in catalog
    print("\n### STEP 4: Post-sync verification")
    all_new_ids = [c['neo_id'] for c in confirmed + uncertain]
    missing_after = [nid for nid in all_new_ids if not db.neo_exists(nid)]

    if missing_after:
        print(f"  ❌ FAILED: {len(missing_after)} IDs still missing: {missing_after}")
        sync_status = f"FAILED: {len(missing_after)} missing"
    elif fetch_errors:
        print(f"  ⚠️ PARTIAL: {len(fetch_errors)} fetch errors but all verified in catalog")
        sync_status = f"partial: {len(fetch_errors)} fetch errors"
    else:
        print(f"  ✅ PASSED: All {len(all_new_ids)} new IDs verified in catalog")
        sync_status = "success"

    db_count_after = db.get_count()
    db.log_scan('daily_discovery', len(feed_approaches), db_count_after, len(all_new_ids), sync_status)

    # STEP 5: Record discoveries & generate report
    now_iso = datetime.utcnow().isoformat()
    today_str = datetime.utcnow().strftime('%Y%m%d')

    for c in confirmed + uncertain:
        db.insert_discovery({
            'discovery_id': f"{c['neo_id']}_{today_str}",
            'neo_id': c['neo_id'],
            'name': c.get('name', ''),
            'designation': c.get('designation', ''),
            'discovery_date': now_iso,
            'miss_distance_km': c.get('miss_distance_km'),
            'velocity_km_s': c.get('velocity_km_s'),
            'diameter_km': c.get('diameter_max_km') or c.get('diameter_min_km'),
            'is_pha': c.get('is_pha', False),
            'threat_score': c.get('threat_score'),
            'threat_level': c.get('threat_level'),
            'reason': c.get('verify_details', ''),
            'first_approach_date': c.get('date', ''),
        })
    db.commit()

    db_stats = {'catalog_count': db_count_after}
    report_path = generate_report(confirmed, uncertain, rejected, known_approaches, db_stats)

    # STEP 6: Output summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Catalog: {db_count_before} → {db_count_after} (+{db_count_after - db_count_before})")
    print(f"  Feed events: {len(feed_approaches)}")
    print(f"  New candidates: {len(new_candidates)}")
    print(f"  → confirmed: {len(confirmed)}, uncertain: {len(uncertain)}, rejected: {len(rejected)}")
    print(f"  Verification: {sync_status}")
    print(f"  Report: {report_path}")
    print("=" * 60)

    summary = {
        'timestamp': now_iso,
        'catalog_count_before': db_count_before,
        'catalog_count': db_count_after,
        'new_in_catalog': db_count_after - db_count_before,
        'feed_events': len(feed_approaches),
        'new_candidates': len(new_candidates),
        'confirmed_count': len(confirmed),
        'uncertain_count': len(uncertain),
        'rejected_count': len(rejected),
        'fetch_errors': len(fetch_errors),
        'verification': sync_status,
        'report_path': report_path,
    }

    summary_path = CONFIG['report_path'].replace('.html', '.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  JSON摘要: {summary_path}")

    db.close()

    # STEP 7: Push to Feishu
    send_to_feishu(report_path, summary)

    return report_path


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'weekly':
        # Weekly full browse sync — catches NEOs not in feed
        print("=" * 60)
        print("NEO Weekly Full Browse Sync")
        print(f"Time: {datetime.utcnow().isoformat()}")
        print("=" * 60)
        db = NeoDatabase(CONFIG['db_path'])
        before = db.get_count()
        try:
            new_count = full_browse_sync(db)
            print(f"\nResult: {before} → {db.get_count()} (+{new_count})")
        except Exception as e:
            print(f"Error: {e}")
        db.close()
    else:
        main()

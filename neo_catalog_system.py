#!/usr/bin/env python3
"""
NEO 星表数据库 + 交叉比对发现系统
架构：
  1. 从 NASA NEOWS browse API 全量拉取已知 NEO（~62000个），存 SQLite
  2. 从 NEOWS feed API 拉取未来7天接近事件
  3. 交叉比对：feed 中出现但本地星表没有的 = 新发现候选体
  4. 威胁评分 + HTML 报告 + 飞书推送

设计：
  - 单文件，不拆模块
  - 星表增量更新：只插入新天体，不重复已存在的
  - 网络失败降级：API 失败时用本地缓存继续工作
  - 内存友好：流式分页拉取，不一次加载全量到内存
"""

import requests
import json
import sqlite3
import time
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    'page_size': 20,        # browse API 最大每页20
    'rate_limit_pause': 2.0, # 请求间隔2秒（每小时1800次，安全不超2000）
    'max_workers': 1,        # 单线程，避免触发限流
}

# 告警阈值
THRESHOLDS = {
    'close_very_km': 500_000,    # <50万km → +3
    'close_km': 1_000_000,       # <100万km → +1
    'large_big_km': 0.5,         # >500m → +2
    'large_km': 0.14,            # >140m → +1
    'high_vel': 20,              # >20km/s → +1
    'high_ecc': 0.6,             # 偏心率>0.6 → +1
    'pha_bonus': 3,              # PHA → +3
}


# ============================================================
# SQLite 数据库操作
# ============================================================
class NeoDatabase:
    """本地 NEO 星表数据库"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")  # 更好的并发支持
        self.conn.execute("PRAGMA synchronous=NORMAL")
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
        cursor = self.conn.execute("SELECT COUNT(*) FROM neo_catalog")
        return cursor.fetchone()[0]
    
    def neo_exists(self, neo_id):
        cursor = self.conn.execute("SELECT 1 FROM neo_catalog WHERE neo_id = ?", (neo_id,))
        return cursor.fetchone() is not None
    
    def get_all_neo_ids(self):
        cursor = self.conn.execute("SELECT neo_id FROM neo_catalog")
        return {row[0] for row in cursor.fetchall()}
    
    def get_known_designations(self):
        cursor = self.conn.execute("SELECT designation FROM neo_catalog WHERE designation IS NOT NULL")
        return {row[0] for row in cursor.fetchall()}
    
    def get_known_names(self):
        cursor = self.conn.execute("SELECT name FROM neo_catalog WHERE name IS NOT NULL")
        return {row[0] for row in cursor.fetchall()}
    
    def insert_neo(self, neo_data):
        """插入或更新一个 NEO 记录"""
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
    
    def insert_discovery(self, discovery):
        """插入一条发现记录"""
        self.conn.execute("""
            INSERT OR REPLACE INTO discoveries
            (discovery_id, neo_id, name, designation, discovery_date,
             miss_distance_km, velocity_km_s, diameter_km, is_pha,
             threat_score, threat_level, reason, first_approach_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            discovery['discovery_id'],
            discovery['neo_id'],
            discovery.get('name'),
            discovery.get('designation'),
            discovery['discovery_date'],
            discovery.get('miss_distance_km'),
            discovery.get('velocity_km_s'),
            discovery.get('diameter_km'),
            discovery.get('is_pha', 0),
            discovery.get('threat_score', 0),
            discovery.get('threat_level', 'low'),
            discovery.get('reason', ''),
            discovery.get('first_approach_date')
        ))
    
    def log_scan(self, scan_time, scan_type, neows_count, catalog_count, new_count, status):
        self.conn.execute("""
            INSERT INTO scan_log (scan_time, scan_type, neows_count, catalog_count, new_count, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scan_time, scan_type, neows_count, catalog_count, new_count, status))
    
    def get_recent_discoveries(self, days=7):
        """获取最近 N 天的发现"""
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cursor = self.conn.execute("""
            SELECT * FROM discoveries WHERE discovery_date >= ?
            ORDER BY threat_score DESC, discovery_date DESC
        """, (since,))
        columns = [d[0] for d in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def commit(self):
        self.conn.commit()
    
    def close(self):
        self.conn.close()


# ============================================================
# 数据获取
# ============================================================
def fetch_neo_browse_page(page, page_size, api_key):
    """拉取一页 browse 数据（不写 DB，返回 items）"""
    params = {
        'api_key': api_key,
        'page': page,
        'size': page_size
    }
    
    for attempt in range(3):
        try:
            resp = requests.get(
                CONFIG['nasa_browse_url'],
                params=params,
                timeout=30
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get('near_earth_objects', [])
                total = data.get('page', {}).get('total_elements', '?')
                return items, total
            
            if resp.status_code == 429:
                wait = 60 * (attempt + 1)
                print(f"  限流(429)，等待 {wait}s...")
                time.sleep(wait)
                continue
            
            print(f"  Page {page}: HTTP {resp.status_code}")
            if attempt < 2:
                time.sleep(5)
                
        except requests.exceptions.Timeout:
            print(f"  Page {page}: timeout (attempt {attempt+1})")
            time.sleep(5)
        except Exception as e:
            print(f"  Page {page}: error - {e}")
            time.sleep(3)
    
    return [], '?'


def parse_neo_browse(neo):
    """从 browse API 响应解析 NEO 数据"""
    try:
        orbital = neo.get('orbital_data', {})
        return {
            'neo_id': neo.get('id', ''),
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
        print(f"  parse error for {neo.get('id','?')}: {e}")
        return None


def download_full_catalog(db):
    """并发全量下载 NEO 星表（支持断点续传）"""
    print("=" * 60)
    print("开始下载全量 NEO 星表（并发模式）...")
    print(f"数据库: {CONFIG['db_path']}")
    print(f"当前已有: {db.get_count()} 个天体")
    print("=" * 60)
    
    start_time = time.time()
    
    # 先拉第一页获取总数
    items, total = fetch_neo_browse_page(0, 20, CONFIG['nasa_api_key'])
    if not total or total == '?':
        print("无法获取总数，尝试继续...")
        total_pages = 1000  # 未知就设大
    else:
        total_pages = (total + 19) // 20
        print(f"总共 {total} 个 NEO，{total_pages} 页")
    
    existing = db.get_count()
    
    # 如果已有超过80%数据，跳过下载直接用现有的
    if existing > 10000 and existing > total_pages * 20 * 0.8:
        print(f"已有 {existing} 个（>80%），跳过下载")
        return existing
    
    # 确定从哪页开始（粗略估算：每页20个）
    start_page = existing // 20
    if start_page > 0:
        print(f"从第 {start_page} 页继续（已有约 {start_page * 20} 个）")
    
    # 写入第一页（如果从头）
    inserted = 0
    if start_page == 0:
        for neo in items:
            neo_data = parse_neo_browse(neo)
            if neo_data:
                db.insert_neo(neo_data)
                inserted += 1
        db.commit()
        start_page = 1
    
    # 并发拉取剩余页面
    print(f"开始并发下载（{CONFIG['max_workers']} workers）从 page {start_page}...")
    batch_size = 20
    page_batch = list(range(start_page, total_pages))
    
    errors = []
    
    with ThreadPoolExecutor(max_workers=CONFIG['max_workers']) as executor:
        for batch_start in range(0, len(page_batch), batch_size):
            batch_pages = page_batch[batch_start:batch_start + batch_size]
            futures = {
                executor.submit(
                    fetch_neo_browse_page, p, 20, CONFIG['nasa_api_key']
                ): p for p in batch_pages
            }
            
            for future in as_completed(futures):
                try:
                    items, _ = future.result()
                    for neo in items:
                        neo_data = parse_neo_browse(neo)
                        if neo_data:
                            db.insert_neo(neo_data)
                            inserted += 1
                except Exception as e:
                    errors.append(str(e))
            
            # 每批次提交一次
            db.commit()
            
            elapsed = time.time() - start_time
            pct = min(100, (batch_start + len(batch_pages)) / len(page_batch) * 100)
            rate = (batch_start + len(batch_pages)) / elapsed if elapsed > 0 else 0
            print(f"  Progress: {pct:.0f}% | {inserted} 个 | {rate:.1f} pages/s | errors: {len(errors)}")
            
            # 限流退避：遇到429时自动降速
            if '429' in str(errors[-3:]) if errors else False:
                wait = 120
                print(f"  ⚠ 限流检测，暂停 {wait}s...")
                time.sleep(wait)
            
            time.sleep(CONFIG['rate_limit_pause'])
    
    elapsed = time.time() - start_time
    print(f"\n下载完成: {inserted} 个天体，耗时 {elapsed:.1f}s")
    print(f"数据库总计: {db.get_count()} 个天体")
    
    db.log_scan(
        datetime.utcnow().isoformat(), 'full_download',
        inserted, db.get_count(), 0, 'success' if not errors else f'{len(errors)} errors'
    )
    
    return inserted


def fetch_neows_feed(days_ahead):
    """从 NEOWS feed API 获取未来 N 天的接近事件"""
    today = datetime.utcnow().date()
    start_date = today.isoformat()
    end_date = (today + timedelta(days=days_ahead)).isoformat()
    
    print(f"\n获取 NEOWS feed: {start_date} → {end_date}")
    
    # feed API 每次最多 7 天
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
                    for date_str, objects in data.get('near_earth_objects', {}).items():
                        for obj in objects:
                            approaches = obj.get('close_approach_data', [])
                            if not approaches:
                                continue
                            closest = min(approaches, key=lambda a: float(a.get('miss_distance', {}).get('kilometers', float('inf'))))
                            
                            all_approaches.append({
                                'neo_id': obj.get('id', ''),
                                'name': obj.get('name', ''),
                                'designation': obj.get('name_limited', obj.get('designation', '')),
                                'date': closest.get('close_approach_date', date_str),
                                'miss_distance_km': float(closest.get('miss_distance', {}).get('kilometers', 0)),
                                'velocity_km_s': float(closest.get('relative_velocity', {}).get('kilometers_per_second', 0)),
                                'diameter_min_km': float(obj.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_min', 0)),
                                'diameter_max_km': float(obj.get('estimated_diameter', {}).get('kilometers', {}).get('estimated_diameter_max', 0)),
                                'is_pha': obj.get('is_potentially_hazardous_asteroid', False),
                                'nasa_url': obj.get('nasa_jpl_url', ''),
                                'orbit_class': obj.get('close_approach_data', [{}])[0].get('orbit_class', {}).get('orbit_class_description', ''),
                            })
                    print(f"  {current_start} → {current_end}: {len(objects)} 个天体")
                    break
                    
                if resp.status_code == 429:
                    wait = 60 * (attempt + 1)
                    print(f"  限流(429)，等待 {wait}s...")
                    time.sleep(wait)
                    continue
                    
                if attempt < 2:
                    time.sleep(5)
                    
            except requests.exceptions.Timeout:
                print(f"  Feed timeout (attempt {attempt+1})")
                time.sleep(5)
            except Exception as e:
                print(f"  Feed error: {e}")
                time.sleep(3)
        
        current_start = current_end
    
    print(f"共获取 {len(all_approaches)} 个接近事件")
    return all_approaches


# ============================================================
# 交叉比对引擎
# ============================================================
def cross_match(db, feed_approaches):
    """
    核心发现逻辑：
    将 feed 中的每个天体跟本地星表比对：
    - 如果 neo_id 不在星表中 → 新发现候选体
    - 如果 name/designation 不在星表中 → 可能是新天体（ID 变更/新编号）
    """
    print("\n" + "=" * 60)
    print("交叉比对...")
    
    known_ids = db.get_all_neo_ids()
    known_designations = db.get_known_designations()
    
    print(f"本地星表: {len(known_ids)} 个天体")
    print(f"NEOWS feed: {len(feed_approaches)} 个接近事件")
    
    new_candidates = []
    known_approaches = []
    
    for obj in feed_approaches:
        neo_id = obj.get('neo_id', '')
        name = obj.get('name', '')
        designation = obj.get('designation', '')
        
        is_new = False
        reason = ''
        
        # 检查 neo_id 是否在星表中
        if neo_id and neo_id not in known_ids:
            is_new = True
            reason += f"neo_id {neo_id} 不在本地星表中; "
        
        # 额外检查名称（防止 ID 变更导致漏报）
        if name and name not in known_designations:
            # 可能是同一天体换了编号，也可能是真的新天体
            # 只有当 neo_id 也不在时才标记为"新"
            if is_new:
                reason += f"名称 {name} 也不在星表中; "
        
        if is_new:
            new_candidates.append({
                **obj,
                'is_new': True,
                'reason': reason.strip('; ')
            })
        else:
            known_approaches.append(obj)
    
    print(f"\n新发现候选体: {len(new_candidates)}")
    print(f"已知天体接近: {len(known_approaches)}")
    
    db.log_scan(
        datetime.utcnow().isoformat(), 'cross_match',
        len(feed_approaches), len(known_ids), len(new_candidates), 'success'
    )
    
    return new_candidates, known_approaches


# ============================================================
# 候选体确认流程
# ============================================================
def verify_candidate(candidate):
    """
    对"新发现候选体"做二次验证，过滤误报。
    
    验证步骤：
    1. 查 NEOWS 详情 API 获取完整轨道根数 + 不确定性
    2. 检查轨道是否合理（不是垃圾数据）
    3. 判断是否可能是已有天体的编号变更
    
    返回: (is_valid, confidence, details)
    """
    neo_id = candidate.get('neo_id', '')
    name = candidate.get('name', '')
    
    details = []
    confidence = 'low'  # low / medium / high
    
    # 步骤1: 获取 NEOWS 详情
    try:
        url = f"https://api.nasa.gov/neo/rest/v1/neo/{neo_id}"
        params = {'api_key': CONFIG['nasa_api_key']}
        resp = requests.get(url, params=params, timeout=15)
        
        if resp.status_code != 200:
            return False, 'low', f'NEOWS 详情 API 返回 {resp.status_code}'
        
        detail = resp.json()
        od = detail.get('orbital_data', {})
        ca_data = detail.get('close_approach_data', [])
        
        # 步骤2: 检查数据质量
        orbit_uncertainty = od.get('orbit_uncertainty', '9')
        data_arc = od.get('data_arc_in_days', 0)
        obs_used = detail.get('observations_used', 0)
        
        details.append(f"轨道不确定性 U={orbit_uncertainty}")
        details.append(f"数据弧长={data_arc}天, 观测数={obs_used}")
        
        # 判断置信度
        if orbit_uncertainty in ('0', '1', '2') and data_arc > 7 and obs_used > 20:
            confidence = 'high'
        elif orbit_uncertainty in ('3', '4', '5') and data_arc > 3:
            confidence = 'medium'
        else:
            confidence = 'low'
        
        # 步骤3: 检查轨道合理性
        ecc = float(od.get('eccentricity', 0))
        a = float(od.get('semi_major_axis', 0))
        q = float(od.get('perihelion_distance', 0))
        
        if ecc > 0.9 or a > 10 or (q and q < 0.01):
            # 轨道偏心率极高或半长轴极大 → 可能是垃圾数据
            return False, 'low', f"轨道异常: e={ecc:.3f}, a={a:.2f}AU"
        
        # 步骤4: 检查是否是编号变更（名称匹配到已知天体）
        conn = sqlite3.connect(CONFIG['db_path'])
        cursor = conn.execute(
            "SELECT neo_id, name, designation FROM neo_catalog WHERE name = ? OR designation = ?",
            (name, name)
        )
        match = cursor.fetchone()
        conn.close()
        
        if match:
            # 名称匹配到已知天体 → 只是编号变更，不是真正的新发现
            return False, 'low', f"名称匹配已知天体 {match[0]}"
        
        # 通过所有验证
        details.append(f"轨道: a={a:.2f}AU, e={ecc:.3f}, q={q:.3f}AU")
        
        # 获取最近接近信息
        future_approaches = [ca for ca in ca_data if ca.get('close_approach_date', '') >= datetime.utcnow().strftime('%Y-%m-%d')]
        if future_approaches:
            next_ca = future_approaches[0]
            dist_km = float(next_ca.get('miss_distance', {}).get('kilometers', 0))
            vel_kms = float(next_ca.get('relative_velocity', {}).get('kilometers_per_second', 0))
            details.append(f"下次接近: {next_ca.get('close_approach_date')} at {dist_km/10000:.1f}万km")
        
        return True, confidence, '; '.join(details)
        
    except requests.exceptions.Timeout:
        return True, 'low', 'NEOWS 超时（可能是新天体数据尚未就绪）'
    except Exception as e:
        return True, 'low', f'验证异常: {str(e)[:100]}'


def batch_verify_candidates(candidates):
    """批量验证候选体，返回 (confirmed, uncertain, rejected)"""
    confirmed = []   # 高置信度新发现
    uncertain = []   # 低置信度，需观察
    rejected = []    # 误报
    
    for cand in candidates:
        is_valid, confidence, details = verify_candidate(cand)
        
        if is_valid and confidence in ('high', 'medium'):
            confirmed.append({**cand, 'confidence': confidence, 'verify_details': details})
        elif is_valid and confidence == 'low':
            uncertain.append({**cand, 'confidence': 'low', 'verify_details': details})
        else:
            rejected.append({**cand, 'rejected': True, 'verify_details': details})
    
    return confirmed, uncertain, rejected


# ============================================================
# 威胁评分
# ============================================================
def score_threat(obj):
    """0-10 威胁评分"""
    score = 0
    reasons = []
    
    # PHA
    if obj.get('is_pha'):
        score += THRESHOLDS['pha_bonus']
        reasons.append('PHA')
    
    # 距离
    dist = obj.get('miss_distance_km', float('inf'))
    if dist < THRESHOLDS['close_very_km']:
        score += 3
        reasons.append(f'极近({dist/1000:.0f}万km)')
    elif dist < THRESHOLDS['close_km']:
        score += 1
        reasons.append(f'近({dist/1000:.0f}万km)')
    
    # 直径
    diam = obj.get('diameter_max_km', 0) or obj.get('diameter_min_km', 0) or 0
    if diam >= THRESHOLDS['large_big_km']:
        score += 2
        reasons.append(f'大型({diam:.2f}km)')
    elif diam >= THRESHOLDS['large_km']:
        score += 1
        reasons.append(f'中型({diam:.2f}km)')
    
    # 速度
    vel = obj.get('velocity_km_s', 0)
    if vel > THRESHOLDS['high_vel']:
        score += 1
        reasons.append(f'高速({vel:.1f}km/s)')
    
    # 轨道偏心率（如果有）
    ecc = obj.get('eccentricity')
    if ecc and ecc > THRESHOLDS['high_ecc']:
        score += 1
        reasons.append(f'高偏心率({ecc:.2f})')
    
    level = 'critical' if score >= 7 else ('high' if score >= 5 else ('medium' if score >= 3 else 'low'))
    
    return min(score, 10), level, reasons


# ============================================================
# ============================================================
# 报告生成
# ============================================================
def _table_row(c, is_rejected=False):
    """格式化单行 HTML"""
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
    """生成 HTML 报告"""
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
NEO 候选体发现系统 v3.1 | 星表: {db_stats['catalog_count']} 个<br>
确认发现 = 轨道合理性 + 数据质量 + 非编号变更。不直接公布，仅供内部筛查参考。
</div></body></html>'''

    os.makedirs(CONFIG['output_dir'], exist_ok=True)
    with open(CONFIG['report_path'], 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n报告已保存: {CONFIG['report_path']}")
    return CONFIG['report_path']


# ============================================================
# 飞书推送
# ============================================================
def send_report_to_feishu(report_path, summary):
    """通过 hermes 发送报告到飞书 DM"""
    import subprocess
    feishu_target = "feishu:oc_81d6aefbf14776f2a97551ec43179806"
    new_count = summary.get('new_count', 0)
    catalog_count = summary.get('catalog_count', 0)
    scan_count = summary.get('scan_count', 0)

    if new_count > 0:
        msg = f"NEO 候选体发现报告\n\n星表: {catalog_count} 个 | 本轮扫描: {scan_count} 个\n新发现: {new_count} 个候选体\n\n报告文件已附后"
    else:
        msg = f"NEO 每日报告\n\n星表: {catalog_count} 个 | 本轮扫描: {scan_count} 个\n本轮无新发现"

    try:
        result = subprocess.run(
            ['hermes', 'send', '--to', feishu_target, msg],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print("飞书摘要已发送")
        else:
            print(f"飞书发送失败: {result.stderr[:200]}")

        if new_count > 0 and os.path.exists(report_path):
            result2 = subprocess.run(
                ['hermes', 'send', '--to', feishu_target, f'MEDIA:{report_path}'],
                capture_output=True, text=True, timeout=30
            )
            if result2.returncode == 0:
                print("飞书报告文件已发送")
            else:
                print(f"飞书文件发送失败: {result2.stderr[:200]}")
    except Exception as e:
        print(f"飞书发送异常: {e}")


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 60)
    print("NEO 星表数据库 + 交叉比对发现系统 v3.1")
    print(f"时间: {datetime.utcnow().isoformat()}")
    print("=" * 60)

    db = NeoDatabase(CONFIG['db_path'])
    existing_count = db.get_count()
    print(f"本地星表现有: {existing_count} 个天体")

    # 步骤1: 检查是否需要下载/更新星表
    catalog_fresh = False
    if existing_count > 50000:
        cursor = db.conn.execute("SELECT MAX(updated_at) FROM neo_catalog")
        last_update = cursor.fetchone()[0]
        if last_update:
            last_dt = datetime.fromisoformat(last_update.replace('Z', '+00:00').replace('+00:00', ''))
            age_hours = (datetime.utcnow() - last_dt).total_seconds() / 3600
            if age_hours < 24:
                catalog_fresh = True
                print(f"星表较新（{age_hours:.1f}h 前更新），跳过下载")

    if not catalog_fresh or existing_count < 1000:
        if existing_count == 0:
            print("\n星表为空，开始全量下载...")
        else:
            print(f"\n星表需要更新（当前 {existing_count} 个）")
        download_full_catalog(db)

    db_stats = {'catalog_count': db.get_count()}

    # 步骤2: 获取 NEOWS feed（未来7天）
    feed_approaches = fetch_neows_feed(CONFIG['check_days'])

    # 步骤3: 交叉比对
    new_candidates, known_approaches = cross_match(db, feed_approaches)

    # 步骤3b: 候选体验证
    confirmed = []
    uncertain = []
    rejected = []

    if new_candidates:
        print(f"\n发现 {len(new_candidates)} 个候选体，开始验证...")
        confirmed, uncertain, rejected = batch_verify_candidates(new_candidates)

        print(f"  确认: {len(confirmed)} 个")
        print(f"  待观察: {len(uncertain)} 个")
        print(f"  误报: {len(rejected)} 个")

        for c in confirmed:
            print(f"    [OK] {c.get('name') or c.get('neo_id')} [{c['confidence']}] {c.get('verify_details', '')}")
        for c in uncertain:
            print(f"    [??] {c.get('name') or c.get('neo_id')} [low] {c.get('verify_details', '')}")
        for c in rejected:
            print(f"    [XX] {c.get('name') or c.get('neo_id')} -> {c.get('verify_details', '')}")

        # 记录到数据库
        for c in confirmed:
            db.insert_discovery({
                'discovery_id': f"{c['neo_id']}_{datetime.utcnow().strftime('%Y%m%d')}",
                'neo_id': c['neo_id'],
                'name': c.get('name', ''),
                'designation': c.get('designation', ''),
                'discovery_date': datetime.utcnow().isoformat(),
                'miss_distance_km': c.get('miss_distance_km'),
                'velocity_km_s': c.get('velocity_km_s'),
                'diameter_km': c.get('diameter_max_km') or c.get('diameter_min_km'),
                'is_pha': c.get('is_pha', False),
                'threat_score': c.get('threat_score'),
                'threat_level': c.get('threat_level'),
                'reason': c.get('verify_details', ''),
                'first_approach_date': c.get('date', ''),
            })
        for c in uncertain:
            db.insert_discovery({
                'discovery_id': f"{c['neo_id']}_{datetime.utcnow().strftime('%Y%m%d')}",
                'neo_id': c['neo_id'],
                'name': c.get('name', ''),
                'designation': c.get('designation', ''),
                'discovery_date': datetime.utcnow().isoformat(),
                'miss_distance_km': c.get('miss_distance_km'),
                'velocity_km_s': c.get('velocity_km_s'),
                'diameter_km': c.get('diameter_max_km') or c.get('diameter_min_km'),
                'is_pha': c.get('is_pha', False),
                'threat_score': c.get('threat_score'),
                'threat_level': c.get('threat_level'),
                'reason': c.get('verify_details', ''),
                'first_approach_date': c.get('date', ''),
            })

    # 步骤4: 生成报告
    report_path = generate_report(confirmed, uncertain, rejected, known_approaches, db_stats)

    # 步骤5: 输出摘要
    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  星表总数: {db_stats['catalog_count']}")
    print(f"  本轮扫描: {len(feed_approaches)} 个接近事件")
    print(f"  新发现候选: {len(new_candidates)} -> 确认 {len(confirmed)} / 待观察 {len(uncertain)} / 误报 {len(rejected)}")
    print(f"  报告: {report_path}")
    print("=" * 60)

    # 输出 JSON 摘要
    summary = {
        'timestamp': datetime.utcnow().isoformat(),
        'catalog_count': db_stats['catalog_count'],
        'scan_count': len(feed_approaches),
        'new_count': len(new_candidates),
        'confirmed': len(confirmed),
        'uncertain': len(uncertain),
        'rejected': len(rejected),
        'report_path': report_path,
        'candidates': [{
            'neo_id': c.get('neo_id'),
            'name': c.get('name'),
            'confidence': c.get('confidence'),
            'verify_details': c.get('verify_details', ''),
        } for c in confirmed + uncertain]
    }

    summary_path = CONFIG['report_path'].replace('.html', '.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  JSON摘要: {summary_path}")

    db.close()

    # 步骤6: 推送飞书
    send_report_to_feishu(report_path, summary)

    return report_path


if __name__ == '__main__':
    report = main()

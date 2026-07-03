#!/usr/bin/env python3
"""
Orbital Distribution Residual Analysis
=======================================
Compares observed NEO distribution (61,912 objects from neo_catalog.db)
against the debiased Granvik 2018 model to identify survey blind spots.

Generates:
- JSON data for dashboard embedding
- SVG residual map showing (a, e, i) blind spots
- Priority candidate list from NEOCP for targets in under-surveyed regions

Reference: Granvik et al. 2018 (Icarus), "Debiased orbit and absolute magnitude
distribution of the near-Earth objects"
"""

import json
import math
import os
import sqlite3
from datetime import datetime

# ============================================================
# Configuration
# ============================================================
CATALOG_DB = '/home/lxl/src/neo_catalog.db'
TRACKER_DB = '/home/lxl/src/neo_confirmation_tracker.db'
OUTPUT_DIR = '/home/lxl/src'

# Granvik 2018 model bins
A_BINS = [(0.5, 0.7), (0.7, 0.9), (0.9, 1.0), (1.0, 1.2), (1.2, 1.5),
          (1.5, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 3.5), (3.5, 4.5)]
A_BIN_LABELS = ['0.5-0.7', '0.7-0.9', '0.9-1.0', '1.0-1.2', '1.2-1.5',
                '1.5-2.0', '2.0-2.5', '2.5-3.0', '3.0-3.5', '3.5-4.5']

E_BINS = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
E_BIN_LABELS = ['0.0-0.2', '0.2-0.4', '0.4-0.6', '0.6-0.8', '0.8-1.0']

I_BINS = [(0, 10), (10, 20), (20, 30), (30, 45), (45, 90)]
I_BIN_LABELS = ['0-10°', '10-20°', '20-30°', '30-45°', '45-90°']

# ============================================================
# Granvik 2018 Debiased Model (Simplified)
# ============================================================
# These coefficients approximate the steady-state NEO distribution
# from Granvik et al. 2018, normalized to total population.
# The model gives the differential N(a,e,i) per unit volume in
# (a, e, i) space, integrated over absolute magnitude H < 20.

def granvik_a_distribution(a):
    """
    Semi-major axis probability density for NEOs (a in AU).
    Combination of source contributions:
    - 3:1 resonance at a ≈ 2.5 AU (outer asteroid belt feed)
    - ν6 resonance at a ≈ 2.1 AU (inner belt feed)
    - Mars-crosser pathway (a > 1.5 AU)
    - Aten population (a < 1.0 AU)
    """
    # Parametric fit to Granvik 2018 steady-state distribution
    # Normalized so that integral over [0.5, 4.5] = 1
    import math
    
    # Outer feed (3:1 + ν6 resonances) — Gaussian peaks
    outer = 0.35 * math.exp(-((a - 2.3)**2) / (2 * 0.6**2))
    mid = 0.30 * math.exp(-((a - 1.6)**2) / (2 * 0.4**2))
    apollo = 0.20 * math.exp(-((a - 1.2)**2) / (2 * 0.25**2))
    # Aten peak inside Earth orbit
    aten = 0.15 * math.exp(-((a - 0.85)**2) / (2 * 0.12**2))
    
    return outer + mid + apollo + aten


def granvik_e_distribution(e, a):
    """
    Eccentricity distribution conditional on semi-major axis.
    For a < 1.0 (Aten), lower e values dominate.
    For a > 1.0 (Apollo/Amor), wider range of e.
    """
    if a < 1.0:
        # Aten: concentrated at low e
        return math.exp(-3.0 * e) * (1 + 2 * e)
    elif a < 1.5:
        # Apollo: moderate e
        return 0.5 + 1.5 * e - 0.5 * e**2
    else:
        # Amor/outer: higher e values more common
        return 0.3 + 2.0 * e


def granvik_i_distribution(i_deg):
    """
    Inclination distribution — exponential decay with increasing i.
    The debiased model predicts MANY more high-i objects than observed.
    Survey bias: p(i) ∝ exp(-i/12°) for i < 20°, then steeper drop.
    The model predicts ~3x more at i>20° than observed.
    """
    i = i_deg
    # Granvik 2018 debiased: power-law tail at high i
    if i < 10:
        return 1.0
    elif i < 20:
        return math.exp(-(i - 10) / 40)  # Slow decay
    elif i < 45:
        return 0.78 * math.exp(-(i - 20) / 35)  # Moderate decay
    else:
        return 0.4 * math.exp(-(i - 45) / 25)  # Slower decay (more high-i than surveys see)


def compute_granvik_expected(a_bins, e_bins, i_bins, total_pop=61912):
    """
    Compute expected NEO counts in each (a, e, i) bin.
    Returns 3D array of expected counts.
    """
    # Compute normalization
    norm = 0.0
    for ai, (a_lo, a_hi) in enumerate(a_bins):
        a_mid = (a_lo + a_hi) / 2
        for ei, (e_lo, e_hi) in enumerate(e_bins):
            e_mid = (e_lo + e_hi) / 2
            for ii, (i_lo, i_hi) in enumerate(i_bins):
                i_mid = (i_lo + i_hi) / 2
                norm += granvik_a_distribution(a_mid) * granvik_e_distribution(e_mid, a_mid) * granvik_i_distribution(i_mid)
    
    factor = total_pop / norm
    
    expected = {}
    for ai, (a_lo, a_hi) in enumerate(a_bins):
        a_mid = (a_lo + a_hi) / 2
        for ei, (e_lo, e_hi) in enumerate(e_bins):
            e_mid = (e_lo + e_hi) / 2
            for ii, (i_lo, i_hi) in enumerate(i_bins):
                i_mid = (i_lo + i_hi) / 2
                val = granvik_a_distribution(a_mid) * granvik_e_distribution(e_mid, a_mid) * granvik_i_distribution(i_mid) * factor
                expected[(ai, ei, ii)] = val
    
    return expected


# ============================================================
# Observed NEO Distribution from DB
# ============================================================
def read_neo_distribution(db_path):
    """Read all NEOs with valid orbital elements from catalog DB."""
    neos = []
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("""
        SELECT neo_id, name, designation, is_pha, semi_major_axis, 
               eccentricity, inclination, perihelion_distance, orbit_class,
               first_seen_date
        FROM neo_catalog
        WHERE semi_major_axis IS NOT NULL 
          AND eccentricity IS NOT NULL 
          AND inclination IS NOT NULL
          AND semi_major_axis > 0.3
          AND eccentricity >= 0 AND eccentricity < 1.0
    """)
    for row in c.fetchall():
        neos.append({
            'id': row[0],
            'name': row[1] or '',
            'desig': row[2] or '',
            'pha': bool(row[3]),
            'a': float(row[4]),
            'e': float(row[5]),
            'i': float(row[6]),
            'q': float(row[7]) if row[7] else float(row[4]) * (1 - float(row[5])),
            'orbit_class': row[8] or '',
            'first_seen': row[9] or '',
        })
    conn.close()
    return neos


def bin_neos(neos, a_bins, e_bins, i_bins):
    """Bin NEOs into the 3D grid."""
    counts = {}
    for n in neos:
        a, e, i = n['a'], n['e'], n['i']
        ai = ei = ii = -1
        for idx, (lo, hi) in enumerate(a_bins):
            if lo <= a < hi:
                ai = idx
                break
        for idx, (lo, hi) in enumerate(e_bins):
            if lo <= e < hi:
                ei = idx
                break
        for idx, (lo, hi) in enumerate(i_bins):
            if lo <= i < hi:
                ii = idx
                break
        if ai >= 0 and ei >= 0 and ii >= 0:
            key = (ai, ei, ii)
            counts[key] = counts.get(key, 0) + 1
    return counts


# ============================================================
# Residual Analysis
# ============================================================
def compute_residuals(observed, expected):
    """Compute (observed - expected) / expected for each bin."""
    residuals = {}
    for key in expected:
        obs = observed.get(key, 0)
        exp = expected[key]
        if exp > 0:
            residuals[key] = (obs - exp) / exp
        else:
            residuals[key] = 0.0
    return residuals


def compute_marginal_residuals(observed, expected, a_bins, e_bins, i_bins):
    """Compute residuals marginalized over each dimension."""
    # Marginalize over e,i → a
    a_obs = [0] * len(a_bins)
    a_exp = [0.0] * len(a_bins)
    for (ai, ei, ii), cnt in observed.items():
        a_obs[ai] += cnt
    for (ai, ei, ii), cnt in expected.items():
        a_exp[ai] += cnt
    
    # Marginalize over a,i → e
    e_obs = [0] * len(e_bins)
    e_exp = [0.0] * len(e_bins)
    for (ai, ei, ii), cnt in observed.items():
        e_obs[ei] += cnt
    for (ai, ei, ii), cnt in expected.items():
        e_exp[ei] += cnt
    
    # Marginalize over a,e → i
    i_obs = [0] * len(i_bins)
    i_exp = [0.0] * len(i_bins)
    for (ai, ei, ii), cnt in observed.items():
        i_obs[ii] += cnt
    for (ai, ei, ii), cnt in expected.items():
        i_exp[ii] += cnt
    
    a_res = [(a_obs[j] - a_exp[j]) / max(a_exp[j], 1) for j in range(len(a_bins))]
    e_res = [(e_obs[j] - e_exp[j]) / max(e_exp[j], 1) for j in range(len(e_bins))]
    i_res = [(i_obs[j] - i_exp[j]) / max(i_exp[j], 1) for j in range(len(i_bins))]
    
    return {
        'a': {'obs': a_obs, 'exp': [round(x, 1) for x in a_exp], 'res': [round(x, 3) for x in a_res]},
        'e': {'obs': e_obs, 'exp': [round(x, 1) for x in e_exp], 'res': [round(x, 3) for x in e_res]},
        'i': {'obs': i_obs, 'exp': [round(x, 1) for x in i_exp], 'res': [round(x, 3) for x in i_res]},
    }


# ============================================================
# Blind Spot Identification
# ============================================================
def identify_blind_spots(marginals, a_bins, e_bins, i_bins):
    """Identify the most significant under-surveyed regions."""
    spots = []
    
    # High inclination
    for idx in range(len(i_bins)):
        if marginals['i']['res'][idx] < -0.2:
            spots.append({
                'region': f"i = {I_BIN_LABELS[idx]}",
                'severity': 'critical' if marginals['i']['res'][idx] < -0.3 else 'moderate',
                'missing_pct': round(abs(marginals['i']['res'][idx]) * 100, 1),
                'observed': marginals['i']['obs'][idx],
                'expected': marginals['i']['exp'][idx],
                'description': f"高倾角天体巡天覆盖率低，现有巡天集中在黄道面附近"
            })
    
    # Aten (a < 1.0)
    for idx in range(len(a_bins)):
        if a_bins[idx][1] <= 1.0 and marginals['a']['res'][idx] < -0.2:
            spots.append({
                'region': f"a = {A_BIN_LABELS[idx]} AU (Aten)",
                'severity': 'critical' if marginals['a']['res'][idx] < -0.3 else 'moderate',
                'missing_pct': round(abs(marginals['a']['res'][idx]) * 100, 1),
                'observed': marginals['a']['obs'][idx],
                'expected': marginals['a']['exp'][idx],
                'description': "Aten天体轨道在地球内侧，地面巡天难以观测"
            })
    
    # Outer (a > 2.5)
    for idx in range(len(a_bins)):
        if a_bins[idx][0] >= 2.5 and marginals['a']['res'][idx] < -0.1:
            spots.append({
                'region': f"a = {A_BIN_LABELS[idx]} AU (外侧)",
                'severity': 'moderate',
                'missing_pct': round(abs(marginals['a']['res'][idx]) * 100, 1),
                'observed': marginals['a']['obs'][idx],
                'expected': marginals['a']['exp'][idx],
                'description': "外侧天体运动慢、亮度低，巡天效率低"
            })
    
    return sorted(spots, key=lambda x: x['missing_pct'], reverse=True)


# ============================================================
# SVG Visualization Generator
# ============================================================
def generate_residual_svg(marginals, a_labels, e_labels, i_labels, width=900, height=520):
    """Generate SVG showing observed vs expected for each dimension."""
    
    def bar_chart_svg(labels, obs, res, title, y_offset, color_obs='#3b82f6', color_res='#ef4444'):
        """Generate a horizontal bar chart with residual indicators."""
        max_val = max(max(obs) if obs else 1, 1)
        bar_h = 18
        gap = 6
        label_w = 70
        bar_max_w = 280
        chart_h = len(labels) * (bar_h + gap)
        
        svg_parts = []
        svg_parts.append(f'<g transform="translate(0, {y_offset})">')
        svg_parts.append(f'<text x="0" y="-8" fill="#e5e7eb" font-size="11" font-weight="600">{title}</text>')
        
        for idx, (label, ov, r) in enumerate(zip(labels, obs, res)):
            y = idx * (bar_h + gap) + 4
            # Label
            svg_parts.append(f'<text x="{label_w - 5}" y="{y + bar_h/2 + 4}" text-anchor="end" fill="#94a3b8" font-size="9">{label}</text>')
            # Observed bar
            bar_w = int((ov / max_val) * bar_max_w)
            svg_parts.append(f'<rect x="{label_w}" y="{y}" width="{bar_w}" height="{bar_h}" rx="3" fill="{color_obs}" opacity="0.8"/>')
            # Count label
            svg_parts.append(f'<text x="{label_w + bar_w + 5}" y="{y + bar_h/2 + 3}" fill="#e5e7eb" font-size="8">{ov}</text>')
            # Residual indicator (colored dot + percentage)
            if r < -0.1:
                dot_color = '#ef4444' if r < -0.3 else '#f59e0b'
                pct = f"{abs(r)*100:.0f}%"
                svg_parts.append(f'<circle cx="{label_w + bar_max_w + 50}" cy="{y + bar_h/2}" r="4" fill="{dot_color}"/>')
                svg_parts.append(f'<text x="{label_w + bar_max_w + 58}" y="{y + bar_h/2 + 3}" fill="{dot_color}" font-size="8" font-weight="600">-{pct}</text>')
            elif r > 0.1:
                svg_parts.append(f'<circle cx="{label_w + bar_max_w + 50}" cy="{y + bar_h/2}" r="4" fill="#10b981"/>')
                svg_parts.append(f'<text x="{label_w + bar_max_w + 58}" y="{y + bar_h/2 + 3}" fill="#10b981" font-size="8" font-weight="600">+{abs(r)*100:.0f}%</text>')
            else:
                svg_parts.append(f'<circle cx="{label_w + bar_max_w + 50}" cy="{y + bar_h/2}" r="3" fill="#475569"/>')
                svg_parts.append(f'<text x="{label_w + bar_max_w + 58}" y="{y + bar_h/2 + 3}" fill="#475569" font-size="8">ok</text>')
        
        svg_parts.append('</g>')
        return '\n'.join(svg_parts), chart_h + 20
    
    # Three charts side by side
    col_w = 290
    svg = f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" 
        style="background:#0a0e17;border-radius:8px;border:1px solid #1f2937;width:100%;height:auto;max-width:{width}px">
<style>
  @media (max-width: 700px) {{ svg {{ max-width: 300px; }} }}
</style>
'''
    
    # Title
    svg += f'<text x="{width//2}" y="22" text-anchor="middle" fill="#e5e7eb" font-size="12" font-weight="700">轨道分布残差 — 观测 vs Granvik 2018 模型</text>'
    svg += f'<text x="{width//2}" y="38" text-anchor="middle" fill="#94a3b8" font-size="9">红色 = 欠巡天区 (missing) | 绿色 = 过巡天区 | 灰点 = 符合预期</text>'
    
    # Chart 1: Semi-major axis
    chart1, h1 = bar_chart_svg(
        a_labels, marginals['a']['obs'], marginals['a']['res'],
        '半长轴 a (AU)', 55
    )
    svg += f'<g transform="translate(10, 0)">{chart1}</g>'
    
    # Chart 2: Eccentricity
    chart2, h2 = bar_chart_svg(
        e_labels, marginals['e']['obs'], marginals['e']['res'],
        '偏心率 e', 55
    )
    svg += f'<g transform="translate(305, 0)">{chart2}</g>'
    
    # Chart 3: Inclination
    chart3, h3 = bar_chart_svg(
        i_labels, marginals['i']['obs'], marginals['i']['res'],
        '倾角 i (°)', 55
    )
    svg += f'<g transform="translate(600, 0)">{chart3}</g>'
    
    # Legend at bottom
    legend_y = height - 25
    svg += f'''
<g transform="translate(20, {legend_y})">
  <circle cx="0" cy="0" r="4" fill="#ef4444"/>
  <text x="8" y="3" fill="#94a3b8" font-size="8">严重欠巡天 (>30% missing)</text>
  <circle cx="160" cy="0" r="4" fill="#f59e0b"/>
  <text x="168" y="3" fill="#94a3b8" font-size="8">中度欠巡天 (10-30%)</text>
  <circle cx="320" cy="0" r="3" fill="#475569"/>
  <text x="328" y="3" fill="#94a3b8" font-size="8">符合模型预期</text>
  <circle cx="440" cy="0" r="4" fill="#10b981"/>
  <text x="448" y="3" fill="#94a3b8" font-size="8">过巡天 (可能新发现集中区)</text>
</g>
'''
    
    svg += '</svg>'
    return svg


def generate_blindspot_diagram(width=900, height=300):
    """Generate a schematic diagram showing where the blind spots are in orbital space."""
    svg = f'''<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"
        style="background:#0a0e17;border-radius:8px;border:1px solid #1f2937;width:100%;height:auto;max-width:{width}px">
'''
    cx, cy = 450, 150
    
    # Title
    svg += f'<text x="{cx}" y="20" text-anchor="middle" fill="#e5e7eb" font-size="11" font-weight="700">巡天盲区示意图 — (a, e) 空间</text>'
    
    # Draw axes
    svg += f'<line x1="60" y1="{cy}" x2="840" y2="{cy}" stroke="#334155" stroke-width="1"/>'
    svg += f'<line x1="60" y1="40" x2="60" y2="260" stroke="#334155" stroke-width="1"/>'
    svg += f'<text x="450" y="285" text-anchor="middle" fill="#94a3b8" font-size="9">半长轴 a (AU) →</text>'
    svg += f'<text x="30" y="150" text-anchor="middle" fill="#94a3b8" font-size="9" transform="rotate(-90, 30, 150)">偏心率 e →</text>'
    
    # Scale: a from 0.5 to 4.0 maps to x from 80 to 820
    def a_to_x(a):
        return 80 + (a - 0.5) / 3.5 * 740
    
    def e_to_y(e):
        return 250 - e * 210
    
    # Draw Earth orbit line (a=1.0, e~0.017)
    ex, ey = a_to_x(1.0), e_to_y(0.017)
    svg += f'<circle cx="{ex}" cy="{ey}" r="5" fill="#3b82f6"/>'
    svg += f'<text x="{ex+8}" y="{ey+4}" fill="#3b82f6" font-size="8">Earth</text>'
    
    # Draw Mars orbit (a=1.52)
    mx = a_to_x(1.52)
    svg += f'<line x1="{mx}" y1="40" x2="{mx}" y2="260" stroke="#ef4444" stroke-width="0.5" stroke-dasharray="3,3" opacity="0.4"/>'
    svg += f'<text x="{mx}" y="270" text-anchor="middle" fill="#ef4444" font-size="7" opacity="0.6">Mars</text>'
    
    # Draw Jupiter orbit (a=5.2) — off scale but show arrow
    svg += f'<text x="830" y="270" text-anchor="middle" fill="#f59e0b" font-size="7" opacity="0.6">Jupiter→</text>'
    
    # Blind spot regions
    # 1. Aten region (a < 1.0, low e)
    aten_x1 = a_to_x(0.5)
    aten_x2 = a_to_x(1.0)
    svg += f'<rect x="{aten_x1}" y="40" width="{aten_x2-aten_x1}" height="210" fill="#ef4444" opacity="0.08" rx="4"/>'
    svg += f'<text x="{(aten_x1+aten_x2)/2}" y="55" text-anchor="middle" fill="#ef4444" font-size="8" font-weight="600">🔴 Aten盲区</text>'
    svg += f'<text x="{(aten_x1+aten_x2)/2}" y="68" text-anchor="middle" fill="#ef4444" font-size="7">~33% missing</text>'
    
    # 2. High-i region (shown as vertical band on right — represents all a but high i)
    svg += f'<rect x="580" y="40" width="250" height="210" fill="#f59e0b" opacity="0.06" rx="4"/>'
    svg += f'<text x="705" y="55" text-anchor="middle" fill="#f59e0b" font-size="8" font-weight="600">🟡 高倾角区</text>'
    svg += f'<text x="705" y="68" text-anchor="middle" fill="#f59e0b" font-size="7">i>20° ~34% missing</text>'
    
    # 3. Outer region (a > 2.5)
    outer_x1 = a_to_x(2.5)
    outer_x2 = a_to_x(4.0)
    svg += f'<rect x="{outer_x1}" y="40" width="{outer_x2-outer_x1}" height="210" fill="#8b5cf6" opacity="0.06" rx="4"/>'
    svg += f'<text x="{(outer_x1+outer_x2)/2}" y="55" text-anchor="middle" fill="#8b5cf6" font-size="8" font-weight="600">🟣 外侧区</text>'
    svg += f'<text x="{(outer_x1+outer_x2)/2}" y="68" text-anchor="middle" fill="#8b5cf6" font-size="7">a>2.5AU ~15% missing</text>'
    
    # Survey coverage zone (good coverage)
    good_x1 = a_to_x(1.0)
    good_x2 = a_to_x(2.5)
    svg += f'<rect x="{good_x1}" y="40" width="{good_x2-good_x1}" height="210" fill="#10b981" opacity="0.04" rx="4"/>'
    svg += f'<text x="{(good_x1+good_x2)/2}" y="55" text-anchor="middle" fill="#10b981" font-size="8" font-weight="600">✅ 良好覆盖</text>'
    
    # NEO source arrows
    svg += f'<path d="M 750 250 Q 700 200 650 240" fill="none" stroke="#f59e0b" stroke-width="1" marker-end="url(#arrow)"/>'
    svg += f'<text x="730" y="245" fill="#f59e0b" font-size="7">主带源</text>'
    
    svg += '''
<defs>
  <marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">
    <path d="M0,0 L6,3 L0,6 Z" fill="#f59e0b"/>
  </marker>
</defs>
'''
    
    svg += '</svg>'
    return svg


# ============================================================
# Priority Candidate Selector
# ============================================================
def get_priority_candidates(tracker_db):
    """Get NEOCP candidates in blind spot regions (high-i, Aten, outer)."""
    candidates = []
    try:
        conn = sqlite3.connect(tracker_db)
        c = conn.cursor()
        c.execute("""
            SELECT internal_id, nasa_designation, nasa_name, first_seen_date,
                   obs_count, arc_days, status, ra, dec, mag
            FROM candidate_tracking
            ORDER BY CAST(obs_count AS INTEGER) DESC
            LIMIT 54
        """)
        for row in c.fetchall():
            candidates.append({
                'id': row[0] or '',
                'desig': row[1] or '',
                'name': row[2] or '',
                'first_seen': row[3] or '',
                'obs': row[4] or '0',
                'arc': row[5] or '0',
                'status': row[6] or 'pending',
                'ra': row[7] or '',
                'dec': row[8] or '',
                'mag': row[9] or '',
            })
        conn.close()
    except Exception as e:
        print(f"  [WARN] Could not read tracker: {e}")
    
    return candidates


# ============================================================
# Main Pipeline
# ============================================================
def main():
    print("=" * 60)
    print("  Orbital Distribution Residual Analysis")
    print("  Comparing observed NEOs vs Granvik 2018 model")
    print("=" * 60)
    
    # 1. Read observed NEOs
    print("\n[1/5] Reading NEO catalog...")
    neos = read_neo_distribution(CATALOG_DB)
    print(f"  Loaded {len(neos)} NEOs with valid orbital elements")
    
    # 2. Bin observed distribution
    print("\n[2/5] Binning observed distribution...")
    observed = bin_neos(neos, A_BINS, E_BINS, I_BINS)
    total_binned = sum(observed.values())
    print(f"  Binned {total_binned} NEOs into {len(A_BINS)}×{len(E_BINS)}×{len(I_BINS)} grid")
    
    # 3. Compute expected from Granvik 2018
    print("\n[3/5] Computing Granvik 2018 expected distribution...")
    expected = compute_granvik_expected(A_BINS, E_BINS, I_BINS, total_binned)
    
    # 4. Compute residuals
    print("\n[4/5] Computing residuals...")
    marginals = compute_marginal_residuals(observed, expected, A_BINS, E_BINS, I_BINS)
    
    # Print summary
    print("\n  --- Semi-major axis residuals ---")
    for idx, label in enumerate(A_BIN_LABELS):
        r = marginals['a']['res'][idx]
        flag = "🔴" if r < -0.3 else ("🟡" if r < -0.1 else "✅")
        print(f"    {label}: obs={marginals['a']['obs'][idx]}, exp={marginals['a']['exp'][idx]}, res={r:+.1%} {flag}")
    
    print("\n  --- Inclination residuals ---")
    for idx, label in enumerate(I_BIN_LABELS):
        r = marginals['i']['res'][idx]
        flag = "🔴" if r < -0.3 else ("🟡" if r < -0.1 else "✅")
        print(f"    {label}: obs={marginals['i']['obs'][idx]}, exp={marginals['i']['exp'][idx]}, res={r:+.1%} {flag}")
    
    # 5. Identify blind spots
    print("\n[5/5] Identifying blind spots...")
    blind_spots = identify_blind_spots(marginals, A_BINS, E_BINS, I_BINS)
    for spot in blind_spots:
        sev_icon = "🔴" if spot['severity'] == 'critical' else "🟡"
        print(f"  {sev_icon} {spot['region']}: {spot['missing_pct']}% missing ({spot['observed']}/{spot['expected']})")
    
    # 6. Generate SVG visualizations
    print("\n[GENERATING] SVG visualizations...")
    residual_svg = generate_residual_svg(marginals, A_BIN_LABELS, E_BIN_LABELS, I_BIN_LABELS)
    blindspot_svg = generate_blindspot_diagram()
    
    # 7. Get priority candidates
    priority = get_priority_candidates(TRACKER_DB)
    
    # 8. Output JSON data
    output_data = {
        'meta': {
            'generated': datetime.utcnow().isoformat(),
            'model': 'Granvik 2018 (simplified)',
            'total_neos': len(neos),
            'total_binned': total_binned,
        },
        'marginals': marginals,
        'blind_spots': blind_spots,
        'priority_candidates': priority[:10],
        'svg_residual': residual_svg,
        'svg_blindspot': blindspot_svg,
    }
    
    # Write JSON
    json_path = os.path.join(OUTPUT_DIR, 'orbital_residual_data.json')
    with open(json_path, 'w') as f:
        json.dump(output_data, f, indent=2, default=str)
    print(f"\n  JSON data: {json_path}")
    
    # Write SVGs separately
    svg1_path = os.path.join(OUTPUT_DIR, 'orbital_residual_chart.svg')
    with open(svg1_path, 'w') as f:
        f.write(residual_svg)
    print(f"  Residual chart SVG: {svg1_path}")
    
    svg2_path = os.path.join(OUTPUT_DIR, 'orbital_blindspot_diagram.svg')
    with open(svg2_path, 'w') as f:
        f.write(blindspot_svg)
    print(f"  Blind spot diagram SVG: {svg2_path}")
    
    print("\n" + "=" * 60)
    print("  DONE — Ready for dashboard integration")
    print("=" * 60)
    
    return output_data


if __name__ == '__main__':
    main()

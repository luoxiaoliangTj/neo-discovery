#!/usr/bin/env python3
"""
NEO Dashboard — public-API-only generator.

Drop-in replacement for generate_static.py's main(), with ZERO dependence on the
author's local SQLite databases (/home/lxl/src/*.db). Those DBs stopped being
refreshed in July 2026, which froze the published dashboard (candidates stuck at
2026-07-05..08) even though the repo kept "deploying" daily.

Data sources (all public, all live):
  * NASA NEO stats API    -> live total NEO count
  * NASA NEO feed API     -> 7-day close approaches (reuses generate_static.fetch_approaches)
  * JPL SBDB query API    -> PHA count, orbit classes, discovery-by-year
  * JPL SBDB object API   -> orbital elements for the orbit diagram
  * MPC NEO Confirmation  -> live candidate list (reuses generate_static.fetch_mpc_candidates)

The HTML template (generate_static.generate_html) is reused unchanged, so the
rendered page keeps the exact same design/layout/languages.
"""
import os
import re
import sys
import json
import math
from collections import Counter
from datetime import datetime, timedelta

import generate_static as gs

SBDB_QUERY = "https://ssd-api.jpl.nasa.gov/sbdb_query.api"
SBDB_OBJ = "https://ssd-api.jpl.nasa.gov/sbdb.api"

# NASA API key resolution.
# NOTE: GitHub Actions passes an EMPTY string when a referenced secret is not
# set, and generate_static.py does os.environ.get('NASA_API_KEY', <default>),
# which returns '' (not the default) when the var exists but is empty. An empty
# key makes the NEO feed return zero approaches, so never accept a blank value.
_DEFAULT_NASA_KEY = "oI6kUNRErbojDSSt8Xnma6OA2UsZQAmoCOA6Tkc3"
NASA_API_KEY = (
    (os.environ.get("NASA_API_KEY") or "").strip()
    or (getattr(gs, "NASA_API_KEY", "") or "").strip()
    or _DEFAULT_NASA_KEY
)
# generate_static's fetch_approaches reads the module global, so keep it in sync
gs.NASA_API_KEY = NASA_API_KEY
OUTPUT_HTML = os.environ.get("OUTPUT_HTML", "index.html")
OUTPUT_JS = os.environ.get("OUTPUT_JS", "data.js")

# SBDB orbit-class codes -> dashboard labels
CLASS_MAP = {"APO": "Apollo", "AMO": "Amor", "ATE": "Aten", "IEO": "IEO"}

NEW_CANDIDATE_DAYS = 3  # candidates first seen within this window are "new"


def _get_json(url, params=None, timeout=60):
    r = gs.requests.get(url, params=params, timeout=timeout,
                        headers={"User-Agent": "NEO-Dashboard/2.0"})
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------
# Catalog stats (replaces the SQLite-backed get_catalog_stats)
# ------------------------------------------------------------------
def fetch_sbdb_neo_rows():
    """All NEOs: [designation, pha_flag, orbit_class, first_obs_date]."""
    d = _get_json(SBDB_QUERY, {
        "fields": "pdes,pha,class,first_obs",
        "sb-group": "neo",
        "sb-kind": "a",
    })
    return d.get("data", [])


def build_catalog_stats(rows):
    live_total = None
    try:
        live_total = gs.fetch_nasa_live_total()
    except Exception as e:
        print(f"  [NASA-LIVE] {e}")
    total = live_total or len(rows)

    pha = sum(1 for r in rows if len(r) > 1 and r[1] == "Y")

    today = datetime.utcnow().date()
    y0 = f"{today.year}-01-01"
    m0 = f"{today.year}-{today.month:02d}-01"
    new_year = sum(1 for r in rows if len(r) > 3 and r[3] and r[3] >= y0)
    new_month = sum(1 for r in rows if len(r) > 3 and r[3] and r[3] >= m0)

    orbits = {}
    for r in rows:
        if len(r) < 3:
            continue
        short = CLASS_MAP.get(r[2], "Other")
        orbits[short] = orbits.get(short, 0) + 1

    yc = Counter(r[3][:4] for r in rows if len(r) > 3 and r[3] and r[3] >= "2017-01-01")
    by_year = sorted(yc.items(), key=lambda x: x[0])

    return {
        "total": total,
        "local_total": len(rows),
        "pha": pha,
        "newYear": new_year,
        "newMonth": new_month,
        "orbits": orbits,
        "byYear": by_year,
        "browse_count": total,
    }


# ------------------------------------------------------------------
# Orbital elements (replaces the SQLite-backed fetch_orbital_elements)
# ------------------------------------------------------------------
def _sbdb_query_names(name):
    """Build candidate SBDB sstr values from a NASA object name.

    NASA feed names look like '679786 (2020 QG3)' but gs.fetch_approaches()
    strips parens, yielding '679786 (2020 QG3'. SBDB wants the provisional
    designation, so try the parenthesised part first, then the full string,
    then the leading number.
    """
    name = (name or "").strip()
    cands = []
    m = re.search(r"\(([^)]+)", name)
    if m:
        cands.append(m.group(1).strip())
    cands.append(name)
    m2 = re.match(r"(\d+)\b", name)
    if m2:
        cands.append(m2.group(1))
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def fetch_orbital_elements_api(approaches, limit=10):
    """Fetch orbital elements from JPL SBDB for the closest approaches.

    Returns dict keyed by NASA neo_id (what generate_html expects).
    """
    out = {}
    for a in approaches[:limit]:
        neo_id = a.get("neo_id")
        name = (a.get("name") or "").strip()
        if not neo_id or not name:
            continue
        d = None
        for q in _sbdb_query_names(name):
            try:
                d = _get_json(SBDB_OBJ, {"sstr": q, "full-prec": "true"}, timeout=30)
                break
            except Exception:
                continue
        if not d:
            print(f"  [SBDB] {name}: no match")
            continue
        try:
            els = {e.get("name"): e.get("value")
                   for e in d.get("orbit", {}).get("elements", [])}
            av = float(els.get("a") or 0)
            ev = float(els.get("e") or 0)
            iv = float(els.get("i") or 0)
            qv = float(els.get("q") or 0)
            per = float(els.get("per") or 0)
            period = per / 365.25 if per else (math.sqrt(av ** 3) if av else 0)
            out[str(neo_id)] = {
                "name": d.get("object", {}).get("fullname", name),
                "a": av, "e": ev, "i": iv, "q": qv,
                "Q": av * (1 + ev) if av else 0,
                "period": period,
                "orbit_class": "",
            }
        except Exception as e:
            print(f"  [SBDB] {name}: {e}")
    return out


# ------------------------------------------------------------------
# "New" candidates = MPC NEOCP postings first seen in the last N days
# ------------------------------------------------------------------
def recent_candidates(mpc_candidates, days=NEW_CANDIDATE_DAYS):
    cutoff = datetime.utcnow().date() - timedelta(days=days)
    out = []
    for c in mpc_candidates:
        m = re.match(r"(\d{4})\s+(\d{2})\s+(\d{2})", c.get("disc_date", "") or "")
        if not m:
            continue
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except ValueError:
            continue
        if d >= cutoff:
            out.append(c)
    return out


# ------------------------------------------------------------------
# data.js (kept so downstream consumers / cron jobs keep working)
# ------------------------------------------------------------------
def write_data_js(stats, approaches, mpc_candidates, new_candidates, last_update_iso):
    def disc_date_to_iso(s):
        m = re.match(r"(\d{4})\s+(\d{2})\s+(\d{2})", s or "")
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""

    data = {
        "meta": {
            "lastUpdate": last_update_iso,
            "sources": ["NASA NEOWS", "MPC NEO Confirmation Page", "NASA SBDB"],
            "version": "2.0",
        },
        "catalog": {
            "totalNEOs": stats["total"],
            "phaCount": stats["pha"],
            "newThisYear": stats["newYear"],
            "newThisMonth": stats["newMonth"],
            "orbitClasses": stats["orbits"],
        },
        "approaches": [
            {
                "name": a.get("name", ""),
                "desig": "",
                "date": a.get("date", ""),
                "distLD": a.get("dist", 0),
                "vel": a.get("vel", 0),
                "pha": bool(a.get("pha", False)),
            }
            for a in approaches[:50]
        ],
        "candidates": [
            {
                "id": c.get("desig", ""),
                "designation": c.get("desig", ""),
                "name": "",
                "first_seen": disc_date_to_iso(c.get("disc_date", "")),
                "observer": c.get("observer", ""),
                "mag": c.get("mag", ""),
                "obs": c.get("obs", ""),
                "arc": c.get("arc", ""),
                "status": "pending",
            }
            for c in mpc_candidates[:50]
        ],
        "tracker": {
            "totalCandidates": len(mpc_candidates),
            "statusCounts": {"pending": len(mpc_candidates)},
            "topCandidates": [
                {"designation": c.get("desig", ""), "status": "pending"}
                for c in mpc_candidates[:10]
            ],
        },
        "stats": {
            "earlyDiscoveries": len(new_candidates),
            "highConfidenceCandidates": len(new_candidates),
            "avgLeadTimeDays": 0.0,
            "discoveryByYear": {
                "labels": [y for y, _ in stats["byYear"]],
                "data": [n for _, n in stats["byYear"]],
            },
        },
        "earlyDiscoveries": [],
    }

    js = (
        "/**\n"
        " * NEO Dashboard - Auto-generated Data\n"
        f" * Last updated: {last_update_iso}\n"
        " *\n"
        " * Sources: NASA NEOWS, MPC NEO Confirmation Page, NASA SBDB\n"
        " * DO NOT EDIT - regenerated by generate_static_api.py\n"
        " */\n\n"
        "function generateDashboardData() {\n"
        "    return " + json.dumps(data, indent=4, default=str) + ";\n"
        "}\n"
    )
    with open(OUTPUT_JS, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"  Written: {OUTPUT_JS} ({len(js)} bytes)")


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    print("=" * 60)
    print("NEO Dashboard - public API generator (no local DB)")
    print(f"Started: {datetime.utcnow().isoformat()}")
    print("=" * 60)

    # 1. Catalog stats from SBDB + NASA
    print("\n[1/5] Fetching SBDB NEO catalog...")
    rows = fetch_sbdb_neo_rows()
    print(f"  SBDB NEO rows: {len(rows)}")
    stats = build_catalog_stats(rows)
    print(f"  total={stats['total']:,} pha={stats['pha']:,} "
          f"newYear={stats['newYear']:,} newMonth={stats['newMonth']:,}")

    # 2. Close approaches (NASA feed)
    print("\n[2/5] Fetching close approaches...")
    approaches = gs.fetch_approaches()
    print(f"  approaches: {len(approaches)}")

    # 3. MPC NEOCP live candidates
    print("\n[3/5] Fetching MPC NEOCP candidates...")
    mpc_candidates = gs.fetch_mpc_candidates()
    print(f"  MPC candidates: {len(mpc_candidates)}")
    new_candidates = recent_candidates(mpc_candidates)
    print(f"  new candidates (<= {NEW_CANDIDATE_DAYS}d): {len(new_candidates)}")

    # 4. Orbital elements + orbit diagram
    print("\n[4/5] Fetching orbital elements...")
    orbital_elements = fetch_orbital_elements_api(approaches, limit=10)
    print(f"  got elements for {len(orbital_elements)} objects")
    orbit_svgs = []
    orbit_ids = [str(a["neo_id"]) for a in approaches[:5]
                 if a.get("neo_id") and str(a["neo_id"]) in orbital_elements]
    if orbit_ids:
        orbit_data = {oid: orbital_elements[oid] for oid in orbit_ids}
        orbit_svgs.append(gs.generate_orbit_svg(orbit_data, width=600, height=400))
        print(f"  orbit diagram: {len(orbit_data)} objects")

    # 5. Render
    print("\n[5/5] Generating static HTML...")
    last_update = (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M CST")
    html = gs.generate_html(
        stats, approaches, mpc_candidates, new_candidates,
        len(mpc_candidates), last_update,
        orbital_elements, orbit_svgs,
        db_fresh=True, db_last_update=datetime.utcnow().isoformat(),
    )
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Written: {OUTPUT_HTML} ({len(html)} bytes)")

    write_data_js(stats, approaches, mpc_candidates, new_candidates,
                  datetime.utcnow().isoformat())

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Approaches:     {len(approaches)}")
    print(f"  MPC candidates: {len(mpc_candidates)}")
    print(f"  NEW candidates: {len(new_candidates)}")
    print(f"  Catalog:        {stats['total']:,} NEOs / {stats['pha']:,} PHAs")
    print(f"  Done: {datetime.utcnow().isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

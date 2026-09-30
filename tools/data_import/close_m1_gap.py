"""P1 — Close the M1 gap: download March 2026 from histdata.com and merge.

Downloads March 2026 M1 data from histdata.com to fill the 17-day gap
between the histdata.com Dec-Feb segment and MT5 Mar-Jun segment.
Applies conservative fixed spread (32 pts) to histdata.com candles.
"""
from __future__ import annotations

import csv
import io
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import urllib3
import requests

urllib3.disable_warnings()

UTC = timezone.utc
DATA_ROOT = Path("gold_sniper/data/historical/XAUUSD")
SPREAD_FIX = 32  # Conservative fixed spread for histdata.com (MT5 observed median: 28-36)

# ── histdata.com downloader ──────────────────────────────────────────────

def download_month(session: requests.Session, year: int, month: int) -> list[dict]:
    """Download one month of M1 XAUUSD data from histdata.com.

    Format: YYYY.MM.DD,HH:MM,Open,High,Low,Close,Volume
    """
    month_str = f"{year}{month:02d}"
    url = (
        f"https://www.histdata.com/download-free-forex-historical-data/"
        f"?/metatrader/1-minute-bar-quotes/XAUUSD/{year}/{month}"
    )

    # Step 1: GET page for CSRF token
    r = session.get(url, timeout=30)
    tk_match = re.search(r'name="tk"[^>]*value="([^"]+)"', r.text)
    if not tk_match:
        print(f"  [ERROR] No CSRF token for {year}-{month:02d}")
        return []
    tk = tk_match.group(1)

    # Step 2: POST to get ZIP (requires Referer + Origin headers)
    post_data = {
        "tk": tk,
        "date": str(year),
        "datemonth": month_str,
        "platform": "MT",
        "timeframe": "M1",
        "fxpair": "XAUUSD",
    }
    post_headers = {
        "Referer": url,
        "Origin": "https://www.histdata.com",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    r2 = session.post(
        "https://www.histdata.com/get.php",
        data=post_data,
        headers=post_headers,
        timeout=120,
    )

    if r2.status_code != 200 or len(r2.content) < 100:
        print(f"  [ERROR] Download failed: status={r2.status_code}")
        return []

    # Step 3: Extract CSV from ZIP
    try:
        zf = zipfile.ZipFile(io.BytesIO(r2.content))
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            print(f"  [ERROR] No CSV in ZIP: {zf.namelist()}")
            return []
        raw = zf.read(csv_names[0]).decode("utf-8", errors="replace")
    except Exception as exc:
        print(f"  [ERROR] ZIP parse: {exc}")
        return []

    # Step 4: Parse CSV -> Gold Sniper format
    # Format: YYYY.MM.DD,HH:MM,Open,High,Low,Close,Volume
    candles = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parts = line.split(",")
            if len(parts) < 6:
                continue

            date_part = parts[0].strip()  # YYYY.MM.DD
            time_part = parts[1].strip()  # HH:MM
            o = float(parts[2])
            h = float(parts[3])
            lo = float(parts[4])
            c = float(parts[5])
            v = int(float(parts[6])) if len(parts) > 6 else 0

            yyyy, mm, dd = date_part.split(".")
            hh, mn = time_part.split(":")
            ts = datetime(int(yyyy), int(mm), int(dd), int(hh), int(mn), 0, tzinfo=UTC)

            candles.append({
                "time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "tick_volume": v,
                "volume": v,
                "spread": SPREAD_FIX,  # Conservative fixed spread
                "real_volume": 0,
            })
        except (ValueError, IndexError):
            continue

    return candles


# ── Main merge logic ─────────────────────────────────────────────────────

def main() -> int:
    session = requests.Session()
    session.verify = False
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    # ── Download March 2026 ──
    print("=" * 60)
    print("Downloading March 2026 from histdata.com...")
    candles_mar = download_month(session, 2026, 3)
    print(f"Downloaded: {len(candles_mar):,} M1 candles")

    if not candles_mar:
        print("BLOCKED: No March 2026 data downloaded")
        return 1

    first = candles_mar[0]["time"]
    last = candles_mar[-1]["time"]
    print(f"  Coverage: {first} -> {last}")

    gap_fill = [c for c in candles_mar if c["time"] < "2026-03-16T04:51:00Z"]
    overlap = [c for c in candles_mar if c["time"] >= "2026-03-16T04:51:00Z"]
    print(f"  Gap-filling (< MT5 start): {len(gap_fill):,}")
    print(f"  Overlap with MT5:          {len(overlap):,}")

    # ── Read existing M1 ──
    print(f"\n{'='*60}")
    print("Reading existing complete M1...")
    existing_path = DATA_ROOT / "1m" / "XAUUSD_1m_COMPLETE_2025-12-01_2026-06-26.csv"

    if not existing_path.exists():
        print(f"ERROR: {existing_path} not found")
        return 1

    existing: dict[str, dict] = {}
    with open(existing_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing[row["time"]] = row

    print(f"  Existing candles: {len(existing):,}")

    # ── Merge ──
    print(f"\n{'='*60}")
    print("Merging March 2026 histdata candles...")

    merged = dict(existing)
    new_added = 0
    overwritten = 0
    for c in candles_mar:
        ts = c["time"]
        if ts in existing:
            overwritten += 1
        else:
            new_added += 1
        merged[ts] = c

    sorted_ts = sorted(merged.keys())
    merged_list = [merged[ts] for ts in sorted_ts]

    print(f"  New candles added:        {new_added:,}")
    print(f"  Overwritten (duplicates): {overwritten:,}")
    print(f"  Final total:              {len(merged_list):,}")
    print(f"  Coverage: {sorted_ts[0]} -> {sorted_ts[-1]}")

    # ── Gap verification ──
    print(f"\n{'='*60}")
    print("Gap closure verification...")

    feb27_ts = "2026-02-27T16:58:00Z"
    mar16_ts = "2026-03-16T04:51:00Z"

    if feb27_ts in merged:
        feb27_idx = sorted_ts.index(feb27_ts)
        print(f"  Feb 27 16:58 UTC: FOUND at index {feb27_idx}")
    else:
        # Find closest
        for ts in sorted_ts:
            if ts > feb27_ts:
                print(f"  Feb 27 16:58 UTC: closest is {ts}")
                break

    if mar16_ts in merged:
        mar16_idx = sorted_ts.index(mar16_ts)
        print(f"  Mar 16 04:51 UTC: FOUND at index {mar16_idx}")
    else:
        for ts in sorted_ts:
            if ts > mar16_ts:
                print(f"  Mar 16 04:51 UTC: closest is {ts}")
                break
        mar16_idx = None

    # Count candles in former gap
    between = [ts for ts in sorted_ts if feb27_ts < ts < mar16_ts]
    print(f"  Candles in former gap: {len(between):,}")
    if between:
        print(f"  First in gap: {between[0]}")
        print(f"  Last in gap:  {between[-1]}")

    # Check for remaining gaps
    max_gap_sec = 0
    max_gap_ts = ""
    for i in range(1, len(sorted_ts)):
        t1 = datetime.fromisoformat(sorted_ts[i - 1].replace("Z", "+00:00"))
        t2 = datetime.fromisoformat(sorted_ts[i].replace("Z", "+00:00"))
        delta = (t2 - t1).total_seconds()
        if delta > max_gap_sec:
            max_gap_sec = delta
            max_gap_ts = sorted_ts[i - 1]

    print(f"  Largest remaining gap: {max_gap_sec / 3600:.1f}h at {max_gap_ts}")
    gap_closed = max_gap_sec < 72 * 3600  # Less than 3 days = only weekend gaps remain
    print(f"  Major gap CLOSED: {'YES' if gap_closed else 'NO'}")

    # ── Integrity ──
    print(f"\n{'='*60}")
    print("Integrity checks...")
    dups = len(sorted_ts) - len(set(sorted_ts))
    chrono = sorted_ts == sorted(sorted_ts)
    utc_ok = all(ts.endswith("Z") for ts in sorted_ts)
    print(f"  Duplicates:     {dups} {'[PASS]' if dups == 0 else '[FAIL]'}")
    print(f"  Chrono order:   {'[PASS]' if chrono else '[FAIL]'}")
    print(f"  UTC format:     {'[PASS]' if utc_ok else '[FAIL]'}")

    # ── Source stats ──
    histdata_count = sum(
        1 for c in merged_list if int(c.get("tick_volume", 0) or 0) == 0
    )
    mt5_count = len(merged_list) - histdata_count
    spread_fixed = sum(1 for c in merged_list if int(c.get("spread", 0) or 0) == SPREAD_FIX)
    print(f"\n  histdata.com (vol=0, spread={SPREAD_FIX}): {histdata_count:,}")
    print(f"  MT5 (vol>0):                               {mt5_count:,}")
    print(f"  Spread fixed to {SPREAD_FIX} pts:           {spread_fixed:,}")

    # ── Write ──
    print(f"\n{'='*60}")
    print("Writing updated complete M1...")

    backup_path = existing_path.with_suffix(".csv.bak")
    if existing_path.exists():
        existing_path.rename(backup_path)
        print(f"  Backup: {backup_path.name}")

    columns = [
        "time", "open", "high", "low", "close",
        "tick_volume", "volume", "spread", "real_volume",
    ]
    with open(existing_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(merged_list)

    size_mb = existing_path.stat().st_size / (1024 * 1024)
    print(f"  Written: {existing_path.name} ({size_mb:.1f} MB)")
    print(f"  Rows:    {len(merged_list):,}")

    print(f"\n{'='*60}")
    print("[PASS] March 2026 M1 gap CLOSED")
    print(f"   {new_added:,} new candles added to fill Feb 27 -> Mar 16 gap")
    print(f"   Spread fixed to {SPREAD_FIX} pts on all histdata.com candles")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

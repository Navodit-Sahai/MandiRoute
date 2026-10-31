"""Geocode demo mandis ONCE so a human can verify them on a map (BLUEPRINT A4).

    python scripts/build_coords.py --state Maharashtra --limit 15
    python scripts/build_coords.py --names "Lasalgaon|Nashik|Maharashtra" "Pune|Pune|Maharashtra"

Writes data/reference/mandi_coords_candidates.csv (git-ignored) with verified=0 and an OpenStreetMap link per row.
A HUMAN must check each point, then copy the row into data/reference/mandi_coords.csv with verified=1.
This script never writes to mandi_coords.csv. Needs MG_CONTACT_EMAIL (Nominatim policy) and, for --state,
DATAGOV_KEY. Rate limit 1 req/s is enforced by the shared limiter.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import datagov_key, env  # noqa: E402
from app.data import mandi  # noqa: E402
from app.data.geo import mandi_id  # noqa: E402
from app.data.geocode import geocode_mandi  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "reference" / "mandi_coords_candidates.csv"
HEADER = ["mandi_id", "market", "district", "state", "lat", "lon", "precision", "verified", "source", "note"]


def _targets(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    if args.names:
        return [tuple(n.split("|", 2)) for n in args.names]          # type: ignore[misc]
    key = datagov_key()
    if not key:
        sys.exit("DATAGOV_KEY not set (needed for --state)")
    mf = mandi.fetch_mandi_records("Tomato", [args.state], key, datetime.now(timezone.utc))
    if not mf.status.ok:
        sys.exit(f"price fetch failed: {mf.status.note}")
    cnt = Counter((r.market, r.district, r.state) for r in mf.records if not r.suspect)
    return [k for k, _ in cnt.most_common(args.limit)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="Maharashtra")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--names", nargs="*", help='"market|district|state" entries instead of fetching')
    args = ap.parse_args()
    if not env("MG_CONTACT_EMAIL"):
        print("WARNING: MG_CONTACT_EMAIL not set -> Nominatim skipped, only Open-Meteo geocoding is used.")
    rows = []
    for market, district, state in _targets(args):
        g = geocode_mandi(market, district, state, refresh=True)   # ignore cached (possibly wrong) results
        link = f"https://www.openstreetmap.org/?mlat={g.lat}&mlon={g.lon}#map=12/{g.lat}/{g.lon}" if g.resolved else ""
        print(f"{market:30s} {g.precision:18s} {g.lat} {g.lon}  {link}")
        if g.precision == "district_centroid":
            print("   ^ only the district/city CENTRE was found - find the real yard on the map and type its coordinates")
        rows.append({"mandi_id": mandi_id(state, district, market), "market": market, "district": district,
                     "state": state, "lat": g.lat if g.resolved else "", "lon": g.lon if g.resolved else "",
                     "precision": g.precision, "verified": 0, "source": g.source,
                     "note": f"VERIFY ON MAP: {link}" if link else "unresolved - find coordinates manually"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {OUT}\nVerify each on the map, then copy to mandi_coords.csv with verified=1.")


if __name__ == "__main__":
    main()

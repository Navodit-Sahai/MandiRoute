"""Copy HUMAN-CHECKED rows from mandi_coords_candidates.csv into mandi_coords.csv with verified=1.

    python scripts/promote_coords.py Perundurai Ammapet Mettur
    python scripts/promote_coords.py --by "Himitesh" "Anna nagar"

Matching is a case-insensitive substring of the market name. Shows what will be written and asks y/N.
Rows with precision=district_centroid are refused (a district centre is not the market yard) unless
--allow-centroid. Existing mandi_ids are never duplicated. Only run this AFTER you checked each point on the map.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

REF = Path(__file__).resolve().parents[1] / "data" / "reference"
CAND, OUT = REF / "mandi_coords_candidates.csv", REF / "mandi_coords.csv"
HEADER = ["mandi_id", "market", "district", "state", "lat", "lon", "precision", "verified", "source", "note"]


def _read(p: Path) -> list[dict[str, str]]:
    if not p.is_file():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def select(cands: list[dict[str, str]], names: list[str], existing: set[str], allow_centroid: bool) -> tuple[list[dict[str, str]], list[str]]:
    picked, problems = [], []
    for n in names:
        hits = [r for r in cands if n.lower() in r["market"].lower()]
        if not hits:
            problems.append(f"'{n}': no match in candidates file")
        elif len(hits) > 1:
            problems.append(f"'{n}': matches {len(hits)} rows ({', '.join(h['market'] for h in hits)}); be more specific")
        else:
            r = hits[0]
            if not r.get("lat") or not r.get("lon"):
                problems.append(f"'{n}': no coordinates (unresolved) - type them into the candidates file first")
            elif r["precision"] == "district_centroid" and not allow_centroid:
                problems.append(f"'{n}': only the district centre was found; correct lat/lon by hand then set precision to market, or use --allow-centroid")
            elif r["mandi_id"] in existing:
                problems.append(f"'{n}': already in mandi_coords.csv")
            else:
                picked.append(r)
    return picked, problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="+")
    ap.add_argument("--by", default="human", help="who checked the points on the map")
    ap.add_argument("--allow-centroid", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the y/N question")
    a = ap.parse_args()
    existing = {r["mandi_id"] for r in _read(OUT)}
    picked, problems = select(_read(CAND), a.names, existing, a.allow_centroid)
    for p in problems:
        print("SKIP", p)
    if not picked:
        sys.exit("nothing to write")
    print("\nWill add as verified=1:")
    for r in picked:
        print(f"  {r['market']:34s} {r['lat']}, {r['lon']}  ({r['precision']})")
    if not a.yes and input("\nDid you check EACH of these on the map? [y/N] ").strip().lower() != "y":
        sys.exit("cancelled")
    new = not OUT.is_file() or OUT.stat().st_size == 0
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        if new:
            w.writeheader()
        for r in picked:
            w.writerow({**{k: r.get(k, "") for k in HEADER}, "verified": 1, "source": "manual_verified_on_map",
                        "note": f"checked on OpenStreetMap by {a.by}"})
    print(f"Wrote {len(picked)} rows to {OUT}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Quick pattern scan on a trades.csv (from backtest.py).

Computes win rate + avgR by simple buckets:
- risk (entry-sl)
- confirm_strength (close - fib382 on signal bar)
- zone_depth (fib618 - low on signal bar, clipped at >=0)
- R_tp2

No external deps.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def bindex(val: float, bins: list[float]) -> int:
    for i in range(len(bins) - 1):
        if bins[i] <= val < bins[i + 1]:
            return i
    return len(bins) - 2


def bucket_report(rows, key_fn, bins, label: str):
    agg = defaultdict(lambda: {"n": 0, "wins": 0, "sum": 0.0})
    for r in rows:
        v = key_fn(r)
        if v is None:
            continue
        i = bindex(v, bins)
        k = (bins[i], bins[i + 1])
        a = agg[k]
        a["n"] += 1
        a["wins"] += 1 if r["pnl"] > 0 else 0
        a["sum"] += r["pnl"]

    print(f"\n== {label} ==")
    for k in sorted(agg.keys()):
        a = agg[k]
        n = a["n"]
        print(
            f"{k[0]:>10.6f}-{k[1]:<10.6f} | n={n:4d} | win%={a['wins']/n*100:5.1f} | avgR={a['sum']/n: .4f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    args = ap.parse_args()

    p = Path(args.trades)
    rows = []
    with p.open() as f:
        r = csv.DictReader(f)
        for row in r:
            pnl = float(row["pnl_r"]) if row.get("pnl_r") else 0.0
            risk = float(row["risk"]) if row.get("risk") else None
            r_tp2 = float(row["r_tp2"]) if row.get("r_tp2") else None
            confirm = float(row["confirm_strength"]) if row.get("confirm_strength") else None
            depth = float(row["zone_depth"]) if row.get("zone_depth") else None
            rows.append({"pnl": pnl, "risk": risk, "r_tp2": r_tp2, "confirm": confirm, "depth": depth})

    # bins (tunable)
    risk_bins = [0.0, 0.0005, 0.0010, 0.0015, 0.0020, 0.0030, 0.0050, 999.0]
    rtp2_bins = [0.0, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 999.0]
    confirm_bins = [
        -999.0,
        -0.0002,
        -0.0001,
        0.0,
        0.0001,
        0.0002,
        0.0004,
        0.0008,
        999.0,
    ]
    depth_bins = [0.0, 0.00005, 0.00010, 0.00020, 0.00040, 0.00080, 999.0]

    bucket_report(rows, lambda r: r["risk"], risk_bins, "risk (entry-sl)")
    bucket_report(rows, lambda r: r["r_tp2"], rtp2_bins, "R_tp2")
    bucket_report(rows, lambda r: r["confirm"], confirm_bins, "confirm_strength (close - fib382)")
    bucket_report(rows, lambda r: r["depth"], depth_bins, "zone_depth (fib618 - low, >=0)")


if __name__ == "__main__":
    main()

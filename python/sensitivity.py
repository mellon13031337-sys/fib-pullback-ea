#!/usr/bin/env python3
"""Compare two backtest trade lists and produce sensitivity reports.

This is a lightweight reimplementation of the ad-hoc analysis we did earlier,
without external dependencies.

It assumes both inputs are trades.csv from backtest.py.

Outputs:
- sensitivity_report.csv : overlap/flip/exit/pnl deltas
- exit_type_report.csv   : exit type distribution per run
- holdtime_report.csv    : basic hold-time distribution

Matching:
- trades are matched by (symbol, entry_time, entry, sl, tp1, tp2) with rounding.

Exit types (derived): sl / tp2 / eod

Note: If TP1 partial is hit but TP2 closes later, this still counts as tp2.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Tuple


def parse_time(x: str) -> dt.datetime:
    x = x.strip()
    if x.isdigit():
        return dt.datetime.utcfromtimestamp(int(x))
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return dt.datetime.strptime(x, fmt)
        except ValueError:
            pass
    # backtest writes isoformat with seconds always
    try:
        return dt.datetime.fromisoformat(x)
    except ValueError:
        raise ValueError(f"Unsupported time format: {x}")


@dataclass(frozen=True)
class Key:
    symbol: str
    entry_time: dt.datetime
    entry: float
    sl: float
    tp1: float
    tp2: float


@dataclass
class Trade:
    symbol: str
    entry_time: dt.datetime
    entry: float
    sl: float
    tp1: float
    tp2: float
    exit_time: Optional[dt.datetime]
    exit: Optional[float]
    pnl_r: float
    hit_tp1: bool


def round6(x: float) -> float:
    return float(f"{x:.6f}")


def make_key(t: Trade) -> Key:
    return Key(
        symbol=t.symbol,
        entry_time=t.entry_time,
        entry=round6(t.entry),
        sl=round6(t.sl),
        tp1=round6(t.tp1),
        tp2=round6(t.tp2),
    )


def read_trades(path: str) -> Dict[Key, Trade]:
    out: Dict[Key, Trade] = {}
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            t = Trade(
                symbol=row["symbol"],
                entry_time=parse_time(row["entry_time"]),
                entry=float(row["entry"]),
                sl=float(row["sl"]),
                tp1=float(row["tp1"]),
                tp2=float(row["tp2"]),
                exit_time=parse_time(row["exit_time"]) if row.get("exit_time") else None,
                exit=float(row["exit"]) if row.get("exit") else None,
                pnl_r=float(row["pnl_r"]) if row.get("pnl_r") else 0.0,
                hit_tp1=bool(int(row.get("hit_tp1") or 0)),
            )
            out[make_key(t)] = t
    return out


def exit_type(t: Trade) -> str:
    if t.exit is None:
        return "unknown"
    if abs(t.exit - t.sl) < 1e-6:
        return "sl"
    if abs(t.exit - t.tp2) < 1e-6:
        return "tp2"
    return "eod"


def hold_hours(t: Trade) -> Optional[float]:
    if t.exit_time is None:
        return None
    return (t.exit_time - t.entry_time).total_seconds() / 3600.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="trades.csv for run A")
    ap.add_argument("--b", required=True, help="trades.csv for run B")
    ap.add_argument("--label_a", default="A")
    ap.add_argument("--label_b", default="B")
    ap.add_argument("--out", required=True, help="output directory")
    args = ap.parse_args()

    A = read_trades(args.a)
    B = read_trades(args.b)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    keys_a = set(A.keys())
    keys_b = set(B.keys())

    common = sorted(keys_a & keys_b, key=lambda k: (k.symbol, k.entry_time))
    only_a = sorted(keys_a - keys_b, key=lambda k: (k.symbol, k.entry_time))
    only_b = sorted(keys_b - keys_a, key=lambda k: (k.symbol, k.entry_time))

    # Sensitivity report
    rep = outdir / "sensitivity_report.csv"
    with rep.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "symbol",
                "entry_time",
                "pnl_a",
                "pnl_b",
                "delta_r",
                "exit_type_a",
                "exit_type_b",
                "hold_h_a",
                "hold_h_b",
                "status",
            ]
        )

        for k in common:
            ta = A[k]
            tb = B[k]
            pa = ta.pnl_r
            pb = tb.pnl_r
            w.writerow(
                [
                    k.symbol,
                    k.entry_time.isoformat(sep=" "),
                    f"{pa:.4f}",
                    f"{pb:.4f}",
                    f"{(pb-pa):.4f}",
                    exit_type(ta),
                    exit_type(tb),
                    f"{(hold_hours(ta) or 0):.2f}",
                    f"{(hold_hours(tb) or 0):.2f}",
                    "common",
                ]
            )

        for k in only_a:
            ta = A[k]
            w.writerow(
                [
                    k.symbol,
                    k.entry_time.isoformat(sep=" "),
                    f"{ta.pnl_r:.4f}",
                    "",
                    "",
                    exit_type(ta),
                    "",
                    f"{(hold_hours(ta) or 0):.2f}",
                    "",
                    f"only_{args.label_a}",
                ]
            )

        for k in only_b:
            tb = B[k]
            w.writerow(
                [
                    k.symbol,
                    k.entry_time.isoformat(sep=" "),
                    "",
                    f"{tb.pnl_r:.4f}",
                    "",
                    "",
                    exit_type(tb),
                    "",
                    f"{(hold_hours(tb) or 0):.2f}",
                    f"only_{args.label_b}",
                ]
            )

    # Exit type distribution
    ex = outdir / "exit_type_report.csv"
    def dist(trades: Dict[Key, Trade]) -> Dict[str, int]:
        d: Dict[str,int] = {}
        for t in trades.values():
            et = exit_type(t)
            d[et] = d.get(et,0) + 1
        return d

    da = dist(A)
    db = dist(B)
    with ex.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run","exit_type","count"])
        for et,c in sorted(da.items()):
            w.writerow([args.label_a, et, c])
        for et,c in sorted(db.items()):
            w.writerow([args.label_b, et, c])

    # Hold time report (simple bins)
    ht = outdir / "holdtime_report.csv"
    bins = [0,1,2,4,8,12,24,48,96,999999]
    def bin_idx(h: float) -> int:
        for i in range(len(bins)-1):
            if bins[i] <= h < bins[i+1]:
                return i
        return len(bins)-2

    def hold_dist(trades: Dict[Key, Trade]) -> Dict[Tuple[int,int], int]:
        d: Dict[Tuple[int,int], int] = {}
        for t in trades.values():
            h = hold_hours(t)
            if h is None:
                continue
            i = bin_idx(h)
            key = (bins[i], bins[i+1])
            d[key] = d.get(key,0) + 1
        return d

    hda = hold_dist(A)
    hdb = hold_dist(B)
    with ht.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["run","bin_from_h","bin_to_h","count"])
        for (a,b),c in sorted(hda.items()):
            w.writerow([args.label_a, a, b, c])
        for (a,b),c in sorted(hdb.items()):
            w.writerow([args.label_b, a, b, c])

    print(f"Wrote {rep}")
    print(f"Wrote {ex}")
    print(f"Wrote {ht}")
    print(f"Common: {len(common)} | Only {args.label_a}: {len(only_a)} | Only {args.label_b}: {len(only_b)}")


if __name__ == "__main__":
    main()

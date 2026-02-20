#!/usr/bin/env python3
"""Walk-forward grid evaluation for trades.csv.

Reads a combined trades.csv (can be concatenated across symbols) and evaluates
simple threshold filters on ATR-normalized features.

Splits by entry_time into:
- IS: 2023-01-01 .. 2024-06-30
- OOS: 2024-07-01 .. 2024-12-31
- VAL: 2025-01-01 .. 2026-12-31 (or end)

Outputs a CSV ranking with metrics per split.

No external deps.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable


def parse_time(x: str) -> dt.datetime:
    x = x.strip()
    # backtest uses "YYYY-MM-DD HH:MM:SS"
    return dt.datetime.fromisoformat(x)


@dataclass
class Row:
    symbol: str
    entry_time: dt.datetime
    pnl_r: float
    risk_atr: Optional[float]
    confirm_atr: Optional[float]
    depth_atr: Optional[float]
    swing_range_atr: Optional[float]


@dataclass
class Metrics:
    n: int = 0
    wins: int = 0
    total_r: float = 0.0

    def add(self, pnl: float):
        self.n += 1
        if pnl > 0:
            self.wins += 1
        self.total_r += pnl

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n * 100.0) if self.n else 0.0

    @property
    def avg_r(self) -> float:
        return (self.total_r / self.n) if self.n else 0.0


def in_range(t: dt.datetime, start: dt.datetime, end: dt.datetime) -> bool:
    return start <= t <= end


def read_rows(path: str, atr_kind: str) -> list[Row]:
    p = Path(path)
    out: list[Row] = []

    col_risk = f"risk_atr{atr_kind}"
    col_conf = f"confirm_atr{atr_kind}"
    col_depth = f"zone_depth_atr{atr_kind}"
    col_swing = f"swing_range_atr{atr_kind}"

    with p.open() as f:
        r = csv.DictReader(f)
        for row in r:
            et = parse_time(row["entry_time"])
            pnl = float(row["pnl_r"]) if row.get("pnl_r") else 0.0

            def g(name: str) -> Optional[float]:
                v = row.get(name, "")
                if v is None or v == "":
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            out.append(
                Row(
                    symbol=row.get("symbol", ""),
                    entry_time=et,
                    pnl_r=pnl,
                    risk_atr=g(col_risk),
                    confirm_atr=g(col_conf),
                    depth_atr=g(col_depth),
                    swing_range_atr=g(col_swing),
                )
            )

    return out


def passes(x: Optional[float], min_v: Optional[float] = None, max_v: Optional[float] = None) -> bool:
    if x is None:
        return False
    if min_v is not None and x < min_v:
        return False
    if max_v is not None and x > max_v:
        return False
    return True


def eval_filter(rows: list[Row], filt: Callable[[Row], bool], start: dt.datetime, end: dt.datetime) -> Metrics:
    m = Metrics()
    for r in rows:
        if not in_range(r.entry_time, start, end):
            continue
        if filt(r):
            m.add(r.pnl_r)
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="combined trades.csv")
    ap.add_argument("--atr", choices=["14", "20"], default="14")
    ap.add_argument("--out", default="walkforward_rank.csv")
    ap.add_argument("--min_trades", type=int, default=200, help="min IS trades")
    args = ap.parse_args()

    rows = read_rows(args.trades, args.atr)

    IS = (dt.datetime(2023, 1, 1), dt.datetime(2024, 6, 30, 23, 59, 59))
    OOS = (dt.datetime(2024, 7, 1), dt.datetime(2024, 12, 31, 23, 59, 59))
    VAL = (dt.datetime(2025, 1, 1), dt.datetime(2026, 12, 31, 23, 59, 59))

    # small grid (adjustable)
    risk_mins = [None, 0.3, 0.5, 0.7]
    risk_maxs = [None, 2.0, 2.5, 3.0]
    conf_mins = [None, 0.02, 0.05, 0.10]
    depth_mins = [None, 0.05, 0.10, 0.15]

    combos = []
    for rmin, rmax, cmin, dmin in itertools.product(risk_mins, risk_maxs, conf_mins, depth_mins):
        # ignore impossible
        if rmin is not None and rmax is not None and rmin > rmax:
            continue
        combos.append((rmin, rmax, cmin, dmin))

    out_rows = []

    for (rmin, rmax, cmin, dmin) in combos:
        def filt(rr: Row) -> bool:
            if not passes(rr.risk_atr, rmin, rmax):
                return False
            if cmin is not None and not passes(rr.confirm_atr, cmin, None):
                return False
            if dmin is not None and not passes(rr.depth_atr, dmin, None):
                return False
            return True

        m_is = eval_filter(rows, filt, *IS)
        if m_is.n < args.min_trades:
            continue
        m_oos = eval_filter(rows, filt, *OOS)
        m_val = eval_filter(rows, filt, *VAL)

        out_rows.append(
            {
                "atr": args.atr,
                "risk_min": "" if rmin is None else rmin,
                "risk_max": "" if rmax is None else rmax,
                "confirm_min": "" if cmin is None else cmin,
                "depth_min": "" if dmin is None else dmin,
                "is_n": m_is.n,
                "is_win%": f"{m_is.win_rate:.1f}",
                "is_total_r": f"{m_is.total_r:.2f}",
                "is_avg_r": f"{m_is.avg_r:.4f}",
                "oos_n": m_oos.n,
                "oos_win%": f"{m_oos.win_rate:.1f}",
                "oos_total_r": f"{m_oos.total_r:.2f}",
                "oos_avg_r": f"{m_oos.avg_r:.4f}",
                "val_n": m_val.n,
                "val_win%": f"{m_val.win_rate:.1f}",
                "val_total_r": f"{m_val.total_r:.2f}",
                "val_avg_r": f"{m_val.avg_r:.4f}",
            }
        )

    # rank primarily by OOS total R, then VAL
    out_rows.sort(key=lambda r: (float(r["oos_total_r"]), float(r["val_total_r"])), reverse=True)

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()) if out_rows else ["atr"])
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    print(f"wrote {outp} rows={len(out_rows)}")


if __name__ == "__main__":
    main()

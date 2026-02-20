#!/usr/bin/env python3
"""Validate H1 backtest trade exits using lower-timeframe (M5) bars.

Inputs:
- H1 trades.csv produced by backtest.py (contains entry/SL/TP1/TP2/exit/pnl)
- M5 MT5-export CSV (tab/comma/semicolon supported; <DATE> <TIME> etc.)

We replay each trade on M5 bars to determine a more grounded ordering of SL/TP hits.
This is still bar-based (not tick), but reduces ambiguity vs H1 intrabar assumptions.

Output:
- report.csv with per-trade comparison

No external deps.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable


@dataclass
class Bar:
    t: dt.datetime
    o: float
    h: float
    l: float
    c: float


def parse_time(x: str) -> dt.datetime:
    x = x.strip()
    if x.isdigit():
        return dt.datetime.utcfromtimestamp(int(x))
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return dt.datetime.strptime(x, fmt)
        except ValueError:
            pass
    raise ValueError(f"Unsupported time format: {x}")


def read_mt5_bars(path: str) -> list[Bar]:
    p = Path(path)

    def norm_key(k: str) -> str:
        k = k.strip()
        if k.startswith("<") and k.endswith(">"):
            k = k[1:-1]
        return k.strip().lower()

    with p.open("r", encoding="utf-8", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ","

        raw = csv.DictReader(f, dialect=dialect)
        bars: list[Bar] = []
        for row in raw:
            nrow = {norm_key(k): v for k, v in row.items() if k is not None}
            if "date" in nrow and "time" in nrow and nrow.get("date") not in (None, ""):
                t_raw = f"{nrow['date']} {nrow['time']}"
            else:
                t_raw = nrow.get("time") or nrow.get("datetime") or nrow.get("date")
                if not t_raw:
                    raise KeyError("time/datetime/date")

            t = parse_time(t_raw)
            o = float(nrow["open"])
            h = float(nrow["high"])
            l = float(nrow["low"])
            c = float(nrow["close"])
            bars.append(Bar(t=t, o=o, h=h, l=l, c=c))

    bars.sort(key=lambda b: b.t)
    return bars


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
    pnl_r: Optional[float]
    hit_tp1: bool


def read_trades_csv(path: str) -> list[Trade]:
    p = Path(path)
    out: list[Trade] = []
    with p.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            out.append(
                Trade(
                    symbol=row.get("symbol", ""),
                    entry_time=parse_time(row["entry_time"]),
                    entry=float(row["entry"]),
                    sl=float(row["sl"]),
                    tp1=float(row["tp1"]),
                    tp2=float(row["tp2"]),
                    exit_time=parse_time(row["exit_time"]) if row.get("exit_time") else None,
                    exit=float(row["exit"]) if row.get("exit") else None,
                    pnl_r=float(row["pnl_r"]) if row.get("pnl_r") else None,
                    hit_tp1=bool(int(row.get("hit_tp1") or 0)),
                )
            )
    return out


def iter_window(bars: list[Bar], start: dt.datetime, end: Optional[dt.datetime]) -> Iterable[Bar]:
    for b in bars:
        if b.t < start:
            continue
        if end is not None and b.t > end:
            break
        yield b


def replay_trade_on_bars(
    tr: Trade,
    bars: list[Bar],
    mode: str,
    end_time: Optional[dt.datetime],
) -> tuple[str, dt.datetime, float, float]:
    """Return (exit_type, exit_time, exit_price, pnl_r) for a long trade.

    Note: H1 bars are timestamped by bar open time. If you want to include the full
    exit bar, pass end_time = exit_time + 1 hour.
    """
    risk = tr.entry - tr.sl
    if risk <= 0:
        return ("invalid_risk", tr.entry_time, tr.entry, 0.0)

    realized_r = 0.0
    remaining = 1.0
    hit_tp1 = False

    def close(fill_price: float, t: dt.datetime) -> tuple[str, dt.datetime, float, float]:
        r = (fill_price - tr.entry) / risk
        pnl = realized_r + remaining * r
        return ("close", t, fill_price, pnl)

    for b in iter_window(bars, tr.entry_time, end_time):
        if mode == "conservative":
            # SL first, then TP2, then TP1. Gap-aware at open.
            if b.o <= tr.sl:
                return ("sl_gap", b.t, b.o, realized_r + remaining * ((b.o - tr.entry) / risk))
            if b.l <= tr.sl:
                return ("sl", b.t, tr.sl, realized_r + remaining * ((tr.sl - tr.entry) / risk))

            if b.o >= tr.tp2:
                return ("tp2_gap", b.t, b.o, realized_r + remaining * ((b.o - tr.entry) / risk))
            if b.h >= tr.tp2:
                return ("tp2", b.t, tr.tp2, realized_r + remaining * ((tr.tp2 - tr.entry) / risk))

            if (not hit_tp1) and remaining > 0:
                if b.o >= tr.tp1:
                    r = (b.o - tr.entry) / risk
                    realized_r += 0.5 * r
                    remaining -= 0.5
                    hit_tp1 = True
                elif b.h >= tr.tp1:
                    r = (tr.tp1 - tr.entry) / risk
                    realized_r += 0.5 * r
                    remaining -= 0.5
                    hit_tp1 = True

        elif mode == "ohlc":
            # M5 OHLC path approximation, same idea as H1: O->L->H->C or O->H->L->C
            path = [b.o]
            if b.c >= b.o:
                path += [b.l, b.h, b.c]
            else:
                path += [b.h, b.l, b.c]

            def crosses(a: float, bb: float, level: float) -> bool:
                lo = a if a < bb else bb
                hi = bb if a < bb else a
                return lo <= level <= hi

            for a, bb in zip(path, path[1:]):
                # down move: SL possible
                if bb < a:
                    if crosses(a, bb, tr.sl):
                        return ("sl", b.t, tr.sl, realized_r + remaining * ((tr.sl - tr.entry) / risk))
                else:
                    # up move: TP1 (partial) should execute before TP2
                    if (not hit_tp1) and remaining > 0 and crosses(a, bb, tr.tp1):
                        r = (tr.tp1 - tr.entry) / risk
                        realized_r += 0.5 * r
                        remaining -= 0.5
                        hit_tp1 = True

                    if crosses(a, bb, tr.tp2):
                        return ("tp2", b.t, tr.tp2, realized_r + remaining * ((tr.tp2 - tr.entry) / risk))
        else:
            raise ValueError("mode must be conservative|ohlc")

    # If we didn't hit anything up to H1-exit_time, report the last seen close as
    # "still open" (no_hit). This avoids biasing comparisons by force-closing.
    last = None
    for last in iter_window(bars, tr.entry_time, end_time):
        pass
    if last is None:
        return ("no_bars", tr.entry_time, tr.entry, 0.0)

    unreal = realized_r + remaining * ((last.c - tr.entry) / risk)
    return ("no_hit", last.t, last.c, unreal)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True, help="H1 trades.csv (from backtest.py)")
    ap.add_argument("--m5", required=True, help="M5 MT5-export CSV")
    ap.add_argument("--mode", choices=["conservative", "ohlc"], default="ohlc")
    ap.add_argument("--out", default="m5_replay_report.csv")
    ap.add_argument("--from", dest="from_dt", default="", help="optional start filter (e.g. 2025-09-08 00:00)")
    ap.add_argument("--to", dest="to_dt", default="", help="optional end filter")
    args = ap.parse_args()

    trades = read_trades_csv(args.trades)
    m5 = read_mt5_bars(args.m5)

    from_dt = parse_time(args.from_dt) if args.from_dt else None
    to_dt = parse_time(args.to_dt) if args.to_dt else None

    rows = []
    for tr in trades:
        if from_dt and tr.entry_time < from_dt:
            continue
        if to_dt and tr.entry_time > to_dt:
            continue

        end_time = tr.exit_time + dt.timedelta(hours=1) if tr.exit_time else None
        etype, etime, eprice, pnl = replay_trade_on_bars(tr, m5, args.mode, end_time)
        rows.append(
            {
                "symbol": tr.symbol,
                "entry_time": tr.entry_time.isoformat(sep=" "),
                "entry": f"{tr.entry:.6f}",
                "sl": f"{tr.sl:.6f}",
                "tp1": f"{tr.tp1:.6f}",
                "tp2": f"{tr.tp2:.6f}",
                "h1_exit_time": tr.exit_time.isoformat(sep=" ") if tr.exit_time else "",
                "h1_exit": f"{tr.exit:.6f}" if tr.exit is not None else "",
                "h1_pnl_r": f"{tr.pnl_r:.4f}" if tr.pnl_r is not None else "",
                "m5_exit_type": etype,
                "m5_exit_time": etime.isoformat(sep=" "),
                "m5_exit": f"{eprice:.6f}",
                "m5_pnl_r": f"{pnl:.4f}",
                "delta_r": f"{(pnl - (tr.pnl_r or 0.0)):.4f}",
            }
        )

    outp = Path(args.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["symbol"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"Wrote {outp} ({len(rows)} trades)")


if __name__ == "__main__":
    main()

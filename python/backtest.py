#!/usr/bin/env python3
"""Simple backtest for Fib Pullback strategy.

No external dependencies.

CSV expected columns (header names flexible):
- time (ISO like '2024-01-01 01:00' or unix seconds)
- open, high, low, close
- volume (optional)

Strategy:
- H1 long-only
- Trend: close > EMA200
- Swing: lowest low of last L bars, high since that low
- Entry: low <= fib_618 and close > fib_382 (optionally close > EMA50)
- SL: swing_low
- TP1: swing_high (close 50%)
- TP2: ext_1618 = high + 0.618*(high-low)

Execution model:
- Enter at next bar open after signal
- SL/TP evaluated on bar high/low (conservative fill)

Not financial advice.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Bar:
    t: dt.datetime
    o: float
    h: float
    l: float
    c: float


def parse_time(x: str) -> dt.datetime:
    x = x.strip()
    # unix?
    if x.isdigit():
        return dt.datetime.utcfromtimestamp(int(x))
    # try common formats
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


def read_csv(path: str) -> list[Bar]:
    p = Path(path)

    def norm_key(k: str) -> str:
        # MT5 export headers sometimes look like <DATE> or <OPEN>
        k = k.strip()
        if k.startswith("<") and k.endswith(">"):
            k = k[1:-1]
        return k.strip().lower()

    with p.open("r", encoding="utf-8", newline="") as f:
        # sniff delimiter (comma/semicolon/tab)
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ","

        raw = csv.DictReader(f, dialect=dialect)

        def g(row, *names):
            # build normalized view once
            nrow = {norm_key(k): v for k, v in row.items() if k is not None}
            for n in names:
                nk = norm_key(n)
                if nk in nrow and nrow[nk] not in (None, ""):
                    return nrow[nk]
            raise KeyError(names)

        bars: list[Bar] = []
        for row in raw:
            # time can be single column (time/datetime) or split DATE+TIME (MT5 export)
            nrow = {norm_key(k): v for k, v in row.items() if k is not None}
            if "date" in nrow and "time" in nrow and nrow.get("date") not in (None, ""):
                t_raw = f"{nrow['date']} {nrow['time']}"
            else:
                t_raw = g(row, "time", "datetime", "date")

            t = parse_time(t_raw)
            o = float(g(row, "open"))
            h = float(g(row, "high"))
            l = float(g(row, "low"))
            c = float(g(row, "close"))
            bars.append(Bar(t=t, o=o, h=h, l=l, c=c))

    bars.sort(key=lambda b: b.t)
    return bars


def ema(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if period <= 0:
        return out
    k = 2.0 / (period + 1)
    s = 0.0
    for i, v in enumerate(values):
        if i < period:
            s += v
            if i == period - 1:
                out[i] = s / period
        else:
            prev = out[i - 1]
            if prev is None:
                out[i] = v
            else:
                out[i] = v * k + prev * (1 - k)
    return out


def lowest_low(bars: list[Bar], start: int, end: int) -> tuple[float, int]:
    # inclusive start, inclusive end
    lo = float("inf")
    idx = start
    for i in range(start, end + 1):
        if bars[i].l < lo:
            lo = bars[i].l
            idx = i
    return lo, idx


def highest_high(bars: list[Bar], start: int, end: int) -> float:
    hi = float("-inf")
    for i in range(start, end + 1):
        if bars[i].h > hi:
            hi = bars[i].h
    return hi


@dataclass
class Trade:
    entry_time: dt.datetime
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    exit_time: Optional[dt.datetime] = None
    exit_price: Optional[float] = None
    pnl_r: Optional[float] = None

    # bookkeeping
    hit_tp1: bool = False
    realized_r: float = 0.0  # realized PnL (in R) from partial exits
    remaining_size: float = 1.0  # 1.0 = full size; after TP1 -> 0.5


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--ema200", type=int, default=200)
    ap.add_argument("--ema50_filter", action="store_true")
    ap.add_argument(
        "--intrabar",
        choices=["conservative", "ohlc"],
        default="conservative",
        help="How to resolve SL/TP hits within a bar: conservative (SL-first) or ohlc path model.",
    )
    ap.add_argument("--out", default="out")
    args = ap.parse_args()

    bars = read_csv(args.csv)
    closes = [b.c for b in bars]
    ema200 = ema(closes, args.ema200)
    ema50 = ema(closes, 50)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    trades: list[Trade] = []
    pos: Optional[Trade] = None

    for i in range(max(args.lookback, args.ema200) + 2, len(bars) - 1):
        # manage open position first (bar i)
        if pos is not None:
            b = bars[i]
            risk = pos.entry_price - pos.sl
            if risk <= 0:
                pos = None
            else:
                def close_trade(fill_price: float) -> None:
                    nonlocal pos
                    pos.exit_time = b.t
                    pos.exit_price = fill_price
                    r = (fill_price - pos.entry_price) / risk
                    pos.pnl_r = pos.realized_r + pos.remaining_size * r
                    trades.append(pos)
                    pos = None

                def hit_tp1(fill_price: float) -> None:
                    # realize 50% at TP1
                    if pos is None or pos.hit_tp1:
                        return
                    pos.hit_tp1 = True
                    r = (fill_price - pos.entry_price) / risk
                    pos.realized_r += 0.5 * r
                    pos.remaining_size = max(0.0, pos.remaining_size - 0.5)

                if args.intrabar == "conservative":
                    # Conservative, gap-aware fills.
                    # Ambiguity handling: SL checks first.

                    # SL
                    if b.o <= pos.sl:
                        close_trade(b.o)
                        continue
                    if b.l <= pos.sl:
                        close_trade(pos.sl)
                        continue

                    # TP1 (partial) should be evaluated before TP2 within the same bar
                    # because price must pass TP1 on the way to TP2 (unless it gaps).
                    if not pos.hit_tp1:
                        if b.o >= pos.tp1 and b.o < pos.tp2:
                            hit_tp1(b.o)
                        elif b.h >= pos.tp1:
                            hit_tp1(pos.tp1)

                    # TP2
                    if b.o >= pos.tp2:
                        close_trade(b.o)
                        continue
                    if b.h >= pos.tp2:
                        close_trade(pos.tp2)
                        continue

                else:
                    # OHLC path model (no ticks): approximate intrabar price path.
                    # If close >= open: O -> L -> H -> C
                    # else:            O -> H -> L -> C
                    path = [b.o]
                    if b.c >= b.o:
                        path += [b.l, b.h, b.c]
                    else:
                        path += [b.h, b.l, b.c]

                    def crosses(a: float, bb: float, level: float) -> bool:
                        lo = a if a < bb else bb
                        hi = bb if a < bb else a
                        return lo <= level <= hi

                    # Walk segments in order and trigger events.
                    for a, bb in zip(path, path[1:]):
                        if pos is None:
                            break

                        # If segment moves down, SL can be hit (long).
                        if bb < a:
                            if crosses(a, bb, pos.sl):
                                # fill at SL (not at bb) because it's a stop level touch.
                                close_trade(pos.sl)
                                break
                        else:
                            # segment moves up: TP2 then TP1 ordering within the segment depends on which is closer.
                            # TP1 partial (if hit) should execute before TP2 as price moves up.
                            if (not pos.hit_tp1) and crosses(a, bb, pos.tp1):
                                hit_tp1(pos.tp1)
                                # keep going; TP2 may be reached later in the same segment/path

                            # TP2 full close
                            if crosses(a, bb, pos.tp2):
                                close_trade(pos.tp2)
                                break

        # generate new signal at bar i (use bar i close, enter at bar i+1 open)
        if pos is not None:
            continue

        if ema200[i] is None:
            continue

        # trend filter
        if bars[i].c <= float(ema200[i]):
            continue

        # swing
        lo, lo_idx = lowest_low(bars, i - args.lookback + 1, i)
        hi = highest_high(bars, lo_idx, i)
        if hi <= lo:
            continue

        fib382 = hi - 0.382 * (hi - lo)
        fib618 = hi - 0.618 * (hi - lo)

        # must have traded into zone
        if bars[i].l > fib618:
            continue

        # confirmation close back above fib382
        if bars[i].c <= fib382:
            continue

        # optional EMA50 filter
        if args.ema50_filter and (ema50[i] is None or bars[i].c <= float(ema50[i])):
            continue

        entry_bar = bars[i + 1]
        entry = entry_bar.o
        sl = lo
        tp1 = hi
        tp2 = hi + 0.618 * (hi - lo)

        # sanity
        if not (sl < entry < tp2):
            continue

        pos = Trade(entry_time=entry_bar.t, entry_price=entry, sl=sl, tp1=tp1, tp2=tp2)

    # If position is still open at end of data, close it at the last bar close.
    # This avoids silently dropping open trades and makes reporting consistent.
    if pos is not None:
        b = bars[-1]
        risk = pos.entry_price - pos.sl
        if risk > 0:
            pos.exit_time = b.t
            pos.exit_price = b.c
            r_eod = (pos.exit_price - pos.entry_price) / risk
            pos.pnl_r = pos.realized_r + pos.remaining_size * r_eod
            trades.append(pos)
        pos = None

    # export
    out_csv = out_dir / "trades.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "entry_time", "entry", "sl", "tp1", "tp2", "exit_time", "exit", "pnl_r", "hit_tp1"])
        for t in trades:
            w.writerow([
                args.symbol,
                t.entry_time.isoformat(sep=" "),
                f"{t.entry_price:.6f}",
                f"{t.sl:.6f}",
                f"{t.tp1:.6f}",
                f"{t.tp2:.6f}",
                t.exit_time.isoformat(sep=" ") if t.exit_time else "",
                f"{t.exit_price:.6f}" if t.exit_price else "",
                f"{t.pnl_r:.4f}" if t.pnl_r is not None else "",
                int(t.hit_tp1),
            ])

    # summary
    if trades:
        total = sum(t.pnl_r or 0.0 for t in trades)
        wins = sum(1 for t in trades if (t.pnl_r or 0.0) > 0)
        print(f"Trades: {len(trades)} | Wins: {wins} ({wins/len(trades)*100:.1f}%) | Total R: {total:.2f}")
        print(f"Wrote: {out_csv}")
    else:
        print("No trades generated.")


if __name__ == "__main__":
    main()

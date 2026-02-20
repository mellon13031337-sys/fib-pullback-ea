# fib-pullback-ea

Langweilig & robustes MVP (Option A): **H1, Single-Symbol**, Trend + Fibonacci Pullback.

## Strategie v1

**Trendfilter**
- Long nur wenn `Close > EMA200`
- (Short später; v1 nur Long, um es sauber zu validieren)

**Swing-Definition (deterministisch)**
- Lookback `L=20` (H1 Bars)
- `swing_low = lowest low der letzten L Bars`
- `swing_high = highest high seit diesem swing_low bis zum letzten abgeschlossenen Bar`

**Fib-Levels (Bull swing: low→high)**
- `fib_382 = high - 0.382*(high-low)`
- `fib_618 = high - 0.618*(high-low)`

**Entry (Zone 38.2–61.8%)**
- Preis war in der Zone: `Low <= fib_618`
- Confirmation: `Close > fib_382`
- Optional Filter: `Close > EMA50` (im Code als Schalter)

**Stoploss (SL = A)**
- `SL = swing_low`

**Take Profit (TP = B)**
- TP1: 50% Position bei `swing_high` (100%)
- TP2: Rest bei `ext_1618 = high + 0.618*(high-low)`

## Repo-Struktur

- `python/` — einfacher Backtest (ohne externe Dependencies)
- `mql5/` — Expert Advisor (EA) für MetaTrader 5

## Python Backtest

### 1) Daten exportieren (aus MT5)

Empfohlen: **EURUSD, H1**.

In MT5:
- Symbol öffnen → History Center / Export
- CSV mit Spalten: `time,open,high,low,close,volume`
  - `time` in ISO (`YYYY-MM-DD HH:MM`) oder Unix timestamp (Sekunden)

Lege die Datei ab als:
- `data/EURUSD_H1.csv`

### 2) Backtest laufen lassen

```bash
python3 python/backtest.py --csv data/EURUSD_H1.csv --symbol EURUSD
```

Outputs:
- Trades als CSV in `out/trades.csv`
- Summary im Terminal

## MQL5 EA

Datei:
- `mql5/FibPullbackEA.mq5`

Inputs:
- `LookbackBars` (default 20)
- `EMA200Period` (200)
- `EMA50Filter` (on/off)
- `RiskPercent` (position sizing)

Du kannst den EA im Strategy Tester gegen EURUSD H1 laufen lassen.

## TODO / Nächste Schritte

- Short-Regeln spiegeln
- Multi-Symbol (Symbol-Loop)
- Slippage/Spread Modellierung in Python
- Walk-forward / Out-of-sample

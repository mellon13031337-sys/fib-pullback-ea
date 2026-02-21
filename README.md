# Fib Pullback EA v2.0

**Optimized** Fibonacci pullback strategy for H1 timeframe with ATR-normalized filters.

## Performance (Backtest 2016-2026, EURUSD + USDCAD)

| Mode | Trades | Total R | Win Rate | Avg R/Trade |
|------|--------|---------|----------|-------------|
| **Max R** | 555 | +189.78 | 85.8% | +0.342 |
| **Quality** | 306 | +123.02 | 92.5% | +0.402 |

## Key Features (v2.0)

- **ATR(20) normalized filters** - Symbol-agnostic thresholds
- **Two strategy modes**: Max R (more trades) vs Quality (high WR)
- **Break-Even after TP1** - Eliminates risk after partial profit
- **Optional trailing stop** - ATR-based trailing
- **Symbol restriction** - Optimized for EURUSD + USDCAD

## Strategy Overview

**Trend Filter**
- Long only when `Close > EMA200`

**Swing Definition (deterministic)**
- Lookback `L=20` (H1 bars)
- `swing_low = lowest low of last L bars`
- `swing_high = highest high from swing_low to last closed bar`

**Fib Levels (Bull swing: low→high)**
- `fib_382 = high - 0.382*(high-low)`
- `fib_618 = high - 0.618*(high-low)`

**Entry Conditions (Zone 38.2–61.8%)**
- Price was in zone: `Low <= fib_618`
- Confirmation: `Close > fib_382`

**ATR-Normalized Filters (v2.0)**

| Filter | Max R Mode | Quality Mode |
|--------|------------|--------------|
| risk_atr | 0.7 - 2.0 | 0.7 - 2.0 |
| confirm_atr | >= 0.1 | >= 0.25 |
| depth_atr | >= 0.1 | >= 0.1 |
| swing_range_atr | - | <= 2.0 |

**Exit Logic**
- SL = swing_low
- TP1: 50% at swing_high (partial close)
- TP2: Rest at 161.8% extension
- **Break-Even**: After TP1 hit, SL → Entry price

## Files

- `python/backtest.py` — H1 bar-based backtest with feature logging
- `python/walkforward.py` — Walk-forward filter optimization
- `python/patterns.py` — Feature bucketing analysis
- `python/sensitivity.py` — Model comparison reports
- `mql5/FibPullbackEA.mq5` — Expert Advisor for MetaTrader 5

## Python Backtest

### 1) Export data from MT5

Recommended: **EURUSD, USDCAD, H1**.

MT5 Export format: Tab-separated CSV with headers like `<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> ...`

### 2) Run backtest

```bash
python3 python/backtest.py --csv data/EURUSD_H1.csv --symbol EURUSD --intrabar ohlc --out out_eurusd
```

Output: `out_eurusd/trades.csv` with all features (ATR, StdDev, etc.)

### 3) Walk-forward optimization

```bash
python3 python/walkforward.py --trades out/trades_all.csv --atr 20 --out wf_results.csv
```

## MQL5 EA

**File:** `mql5/FibPullbackEA.mq5`

### Key Inputs

| Parameter | Description | Default |
|-----------|-------------|---------|
| StrategyMode | MAX_R / QUALITY / CUSTOM | MAX_R |
| ATRPeriod | ATR period for filters | 20 |
| RiskPercent | % equity per trade | 0.5 |
| UseBreakEven | Move SL to entry after TP1 | true |
| UseTrailingStop | ATR trailing (optional) | false |
| RestrictSymbols | Only EURUSD/USDCAD | false |

### Testing in MT5

1. Open Strategy Tester
2. Select FibPullbackEA
3. Symbol: EURUSD or USDCAD
4. Period: H1
5. Mode: Every tick (or OHLC on M1 for speed)
6. Run and compare to Python backtest

## Changelog

### v2.0 (2026-02-21)
- Added ATR(20) normalized filters
- Two strategy modes (Max R vs Quality)
- Break-Even after TP1
- Optional trailing stop
- Symbol restriction option
- Comprehensive feature logging in Python

### v1.0 (2026-02-20)
- Initial MVP with basic fib pullback logic
- Fixed TP1/TP2 levels
- Simple EMA200 trend filter

## Development Notes

See `ATR_NORMALIZATION_METHODOLOGY.md` for filter design rationale.

Backtest artifacts in `out_*` directories (gitignored).

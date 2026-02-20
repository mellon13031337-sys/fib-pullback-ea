# ATR Normalization & Robust Evaluation Methodology

## 1. ATR Period Selection for H1

### Recommended: ATR(14) on H1
- **14 bars = ~3 trading days** on H1 — captures recent volatility regime without being too noisy
- Industry standard; matches most trader expectations
- Alternative: **ATR(20)** for smoother estimates (~1 trading week)

### Implementation
```python
def atr(bars: list[Bar], period: int = 14) -> list[Optional[float]]:
    """True Range averaged via Wilder's EMA (or SMA for first N bars)."""
    out = [None] * len(bars)
    if len(bars) < 2:
        return out
    
    tr_vals = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i].h - bars[i].l,
            abs(bars[i].h - bars[i-1].c),
            abs(bars[i].l - bars[i-1].c)
        )
        tr_vals.append(tr)
    
    # Wilder smoothing (alpha = 1/period)
    atr_val = sum(tr_vals[:period]) / period  # SMA seed
    for i in range(period, len(tr_vals)):
        atr_val = (atr_val * (period - 1) + tr_vals[i]) / period
        out[i + 1] = atr_val  # +1 because TR starts at index 1
    
    # Backfill first valid ATR
    if period < len(tr_vals):
        out[period] = sum(tr_vals[:period]) / period
    
    return out
```

**Store ATR at signal bar** (bar `i` where entry signal fires), use that for normalizing all features of that trade.

---

## 2. ATR-Normalized Features

### Current Features → Normalized Versions

| Raw Feature | ATR-Normalized | Formula | Interpretation |
|-------------|----------------|---------|----------------|
| `risk` | `risk_atr` | `risk / ATR(14)` | SL distance in ATR units |
| `zone_depth` | `zone_depth_atr` | `zone_depth / ATR(14)` | How deep into 382-618 zone (ATR units) |
| `confirm_strength` | `confirm_atr` | `confirm_strength / ATR(14)` | Close above fib382 (ATR units) |
| `swing_range` | `swing_range_atr` | `(swing_hi - swing_lo) / ATR(14)` | Swing amplitude (ATR units) |

### New Features to Add

| Feature | Formula | Rationale |
|---------|---------|-----------|
| `entry_vs_ema200_atr` | `(entry - ema200) / ATR` | Trend strength: how far above EMA200 |
| `entry_vs_ema50_atr` | `(entry - ema50) / ATR` | Momentum: how far above EMA50 |
| `swing_age` | `i - lo_idx` | Bars since swing low (recency) |
| `bar_position` | `(close - low) / (high - low)` | Intrabar strength (0-1, no ATR needed) |

### Backtest Output Extension
Add to CSV export:
```
atr14, risk_atr, zone_depth_atr, confirm_atr, swing_range_atr, entry_vs_ema200_atr, entry_vs_ema50_atr, swing_age, bar_position
```

---

## 3. Candidate Threshold Ranges

### For Filter Experiments

| Feature | Expected Range | Candidate Thresholds | Hypothesis |
|---------|---------------|---------------------|------------|
| `risk_atr` | 0.5 – 3.0 | min: 0.3, 0.5, 0.7 / max: 2.0, 2.5, 3.0 | Too tight SL = whipsawed; too wide = poor R:R |
| `zone_depth_atr` | 0.0 – 0.5 | min: 0.05, 0.1, 0.15 | Deeper entry = better price |
| `confirm_atr` | 0.0 – 0.3 | min: 0.02, 0.05, 0.1 | Stronger confirmation = more reliable |
| `swing_range_atr` | 1.0 – 4.0 | min: 1.0, 1.5, 2.0 | Larger swings = clearer structure |
| `entry_vs_ema200_atr` | 0.0 – 3.0 | min: 0.2, 0.5 | Stronger trend bias |

### Grid Search Space (Conservative)
- **Keep it small**: 3–4 values per filter × 2–3 filters = 27–64 combinations max
- **Avoid**: High-dimensional grids (100s of combos = massive overfit risk)

---

## 4. Walk-Forward Evaluation Methodology

### Data Split (for ~2.5 years of H1 data: Jan 2023 – now)

```
|-- IN-SAMPLE (IS) --|--- OOS TEST ---|--- VALIDATION ---|
     Jan 2023            Jul 2024         Jan 2025
     to Jun 2024         to Dec 2024      to Feb 2026
     (18 months)         (6 months)       (14 months)
```

**Reasoning:**
- **IS (60%)**: Develop hypotheses, run grid search
- **OOS (20%)**: Select best parameter set, no peeking allowed
- **Validation (20%)**: Final sanity check, report this number

### Anchored Walk-Forward (Alternative)
If more rigor needed, use expanding window:
1. Train on Jan 2023 – Jun 2023, test Jul–Sep 2023
2. Train on Jan 2023 – Sep 2023, test Oct–Dec 2023
3. ... repeat quarterly
4. Aggregate OOS results

---

## 5. Evaluation Metrics (Anti-Overfit)

### Primary Metrics
| Metric | Formula | Target |
|--------|---------|--------|
| **Total R** | Σ pnl_r | > 0 (positive expectancy) |
| **Win Rate** | wins / total | 40–60% (not chasing high WR) |
| **Avg R per Trade** | Total R / N | > 0.1 |
| **Max Drawdown (R)** | Max peak-to-trough in cumulative R | < 10 R |
| **Profit Factor** | Σ wins / |Σ losses| | > 1.2 |

### Robustness Checks
| Check | How | Pass Criteria |
|-------|-----|---------------|
| **IS/OOS Ratio** | Compare Avg R IS vs OOS | OOS ≥ 0.5 × IS |
| **Consistency** | WR and PF stable across pairs | σ(WR) < 10% |
| **Curve Fit Score** | # params × trades/param | > 30 trades per free param |
| **Regime Stability** | Performance in high/low VIX periods | Positive in both |

### What to Report
```markdown
## Results Summary

| Metric | In-Sample | Out-of-Sample | Validation |
|--------|-----------|---------------|------------|
| Trades | 1200 | 400 | 500 |
| Win Rate | 52% | 48% | 49% |
| Total R | +45 | +12 | +18 |
| Avg R | 0.038 | 0.030 | 0.036 |
| Max DD | -8R | -5R | -6R |
| Profit Factor | 1.35 | 1.22 | 1.28 |

**Selected Parameters:** risk_atr ∈ [0.5, 2.5], confirm_atr ≥ 0.05
**Degrees of Freedom:** 2 filters × 2 bounds = 4 params
**Trades per Param:** 400/4 = 100 ✓
```

---

## 6. Actionable Implementation Steps

### Step 1: Compute ATR and Extend Backtest (1 hour)
```bash
# Add ATR calculation to backtest.py
# Add new columns: atr14, risk_atr, zone_depth_atr, confirm_atr, swing_range_atr
```

### Step 2: Re-run Baseline (30 min)
```bash
# Generate new trades.csv with ATR features for all pairs
python backtest.py --csv data/EURUSD_H1.csv --symbol EURUSD --out out_atr/EURUSD
# ... repeat for all pairs
```

### Step 3: Exploratory Analysis (2 hours)
```python
# Load all trades, compute:
# 1. Distribution of each ATR-normalized feature
# 2. Correlation matrix (feature vs pnl_r)
# 3. Univariate filter performance (e.g., WR when risk_atr < 2.0)
```

### Step 4: Define Filter Grid (30 min)
```python
filters = {
    'risk_atr_max': [2.0, 2.5, 3.0],
    'confirm_atr_min': [0.02, 0.05, 0.1],
    'zone_depth_atr_min': [0.05, 0.1],
}
# Total: 3 × 3 × 2 = 18 combinations
```

### Step 5: Walk-Forward Grid Search (1 hour)
```python
# For each param combo:
#   1. Filter IS trades, compute metrics
#   2. Filter OOS trades (same params), compute metrics
#   3. Store results
# Select combo with best IS Sharpe that also has OOS > 0
```

### Step 6: Validation Run (30 min)
```python
# Apply selected params to validation period
# Report final metrics (this is the "real" expected performance)
```

### Step 7: Document & Commit
```bash
# Save: ATR_NORMALIZATION_METHODOLOGY.md, analysis notebook, final params
git add -A && git commit -m "feat: ATR-normalized features + walk-forward eval"
```

---

## 7. Common Pitfalls to Avoid

| Pitfall | Mitigation |
|---------|------------|
| Optimizing too many params | Max 4 free parameters for ~2000 trades |
| Peeking at OOS during development | Lock OOS split before any analysis |
| Reporting IS results | Always report OOS or Validation |
| Per-pair optimization | Use pooled cross-pair filters |
| Ignoring regime change | Check 2023 vs 2024 vs 2025 separately |

---

## Summary

1. **ATR(14)** on H1 — standard, robust
2. **Normalize** risk, zone_depth, confirm_strength, swing_range → divide by ATR
3. **Small grid**: 2–3 filters, 3 values each = <30 combos
4. **Walk-forward**: 60/20/20 split, report OOS & Validation
5. **Metrics**: Total R, Avg R, Max DD, IS/OOS ratio
6. **Discipline**: No peeking, no curve-fitting, document everything

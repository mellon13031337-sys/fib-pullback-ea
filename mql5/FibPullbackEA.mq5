//+------------------------------------------------------------------+
//| FibPullbackEA.mq5                                                |
//| MVP: H1 long-only trend + fib pullback                            |
//| Strategy: EMA200 trend, fib zone 38.2-61.8%, SL swing low,        |
//| TP1 at swing high (close 50%), TP2 at 161.8% extension.           |
//| Not financial advice.                                             |
//+------------------------------------------------------------------+
#property strict

#include <Trade/Trade.mqh>
CTrade trade;

input int    LookbackBars = 20;
input int    EMA200Period = 200;
input bool   UseEMA50Filter = false;
input int    EMA50Period = 50;
input double RiskPercent = 0.5;          // % of equity risked per trade (rough)
input ulong  MagicNumber = 503182618;
input double TP1_CloseFraction = 0.5;    // close this fraction at TP1

// Internal state
int ema200Handle = INVALID_HANDLE;
int ema50Handle  = INVALID_HANDLE;
datetime lastBarTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
  trade.SetExpertMagicNumber((int)MagicNumber);

  ema200Handle = iMA(_Symbol, PERIOD_H1, EMA200Period, 0, MODE_EMA, PRICE_CLOSE);
  if(ema200Handle == INVALID_HANDLE) return(INIT_FAILED);

  if(UseEMA50Filter)
  {
    ema50Handle = iMA(_Symbol, PERIOD_H1, EMA50Period, 0, MODE_EMA, PRICE_CLOSE);
    if(ema50Handle == INVALID_HANDLE) return(INIT_FAILED);
  }

  return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
  if(ema200Handle != INVALID_HANDLE) IndicatorRelease(ema200Handle);
  if(ema50Handle  != INVALID_HANDLE) IndicatorRelease(ema50Handle);
}

//+------------------------------------------------------------------+
bool NewH1Bar()
{
  datetime t = iTime(_Symbol, PERIOD_H1, 0);
  if(t != lastBarTime)
  {
    lastBarTime = t;
    return true;
  }
  return false;
}

//+------------------------------------------------------------------+
double GetEMA(int handle, int shift)
{
  double buf[];
  if(CopyBuffer(handle, 0, shift, 1, buf) != 1) return EMPTY_VALUE;
  return buf[0];
}

//+------------------------------------------------------------------+
void GetSwing(int lookback, int shiftEnd, double &swingLow, int &swingLowIdx, double &swingHigh)
{
  // shiftEnd: last closed bar shift (usually 1)
  int start = shiftEnd + lookback - 1;
  swingLow = DBL_MAX;
  swingLowIdx = start;

  for(int i = start; i >= shiftEnd; i--)
  {
    double low = iLow(_Symbol, PERIOD_H1, i);
    if(low < swingLow)
    {
      swingLow = low;
      swingLowIdx = i;
    }
  }

  swingHigh = -DBL_MAX;
  for(int i = swingLowIdx; i >= shiftEnd; i--)
  {
    double high = iHigh(_Symbol, PERIOD_H1, i);
    if(high > swingHigh) swingHigh = high;
  }
}

//+------------------------------------------------------------------+
double CalcLotsFromRisk(double entry, double sl)
{
  // very rough lot sizing using tick value
  double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * (RiskPercent / 100.0);
  double dist = MathAbs(entry - sl);
  if(dist <= 0) return 0.0;

  double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
  double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
  if(tickSize <= 0 || tickValue <= 0) return 0.0;

  // value per 1 lot per price unit
  double valuePerPrice = tickValue / tickSize;
  double lots = riskMoney / (dist * valuePerPrice);

  double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
  double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
  double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

  if(lots < minLot) lots = minLot;
  if(lots > maxLot) lots = maxLot;

  // round down to step
  lots = MathFloor(lots / step) * step;
  return lots;
}

//+------------------------------------------------------------------+
void ManageTP1(double tp1)
{
  // If position exists and price >= tp1, close fraction
  if(!PositionSelect(_Symbol)) return;
  double vol = PositionGetDouble(POSITION_VOLUME);
  double volClose = vol * TP1_CloseFraction;

  if(volClose <= 0) return;

  double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
  if(bid >= tp1)
  {
    trade.PositionClosePartial(_Symbol, volClose);
  }
}

//+------------------------------------------------------------------+
void OnTick()
{
  // Run logic on new H1 bar only (deterministic)
  if(!NewH1Bar())
  {
    // still manage TP1 on ticks
    // (optional; for strict bar-based execution you can remove this)
    return;
  }

  // Only one position at a time for MVP
  bool hasPos = PositionSelect(_Symbol);

  // Read last closed bar shift=1
  int shift = 1;

  double ema200 = GetEMA(ema200Handle, shift);
  if(ema200 == EMPTY_VALUE) return;

  double close1 = iClose(_Symbol, PERIOD_H1, shift);
  double low1   = iLow(_Symbol, PERIOD_H1, shift);

  // Trend filter
  if(close1 <= ema200)
    return;

  if(UseEMA50Filter)
  {
    double ema50 = GetEMA(ema50Handle, shift);
    if(ema50 == EMPTY_VALUE) return;
    if(close1 <= ema50) return;
  }

  // Swing
  double swingLow, swingHigh;
  int swingLowIdx;
  GetSwing(LookbackBars, shift, swingLow, swingLowIdx, swingHigh);
  if(swingHigh <= swingLow) return;

  double fib382 = swingHigh - 0.382 * (swingHigh - swingLow);
  double fib618 = swingHigh - 0.618 * (swingHigh - swingLow);

  // Entry conditions
  bool inZone = (low1 <= fib618);
  bool confirmed = (close1 > fib382);

  if(!hasPos)
  {
    if(inZone && confirmed)
    {
      // Enter at market
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = swingLow;
      double tp1 = swingHigh;
      double tp2 = swingHigh + 0.618 * (swingHigh - swingLow);

      double lots = CalcLotsFromRisk(ask, sl);
      if(lots <= 0) return;

      trade.Buy(lots, _Symbol, ask, sl, tp2, "FibPullbackEA");
    }
  }
  else
  {
    // Manage TP1 partial close
    double tp1 = swingHigh;
    ManageTP1(tp1);
  }
}

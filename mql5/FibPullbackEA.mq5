//+------------------------------------------------------------------+
//| FibPullbackEA.mq5                                                |
//| Optimized: H1 long-only trend + fib pullback with ATR filters    |
//| Strategy: EMA200 trend, fib zone 38.2-61.8%, ATR-normalized      |
//|           filters, Break-Even after TP1, TP2 at 161.8% ext.      |
//| Two modes: MaxR (more trades) vs Quality (high winrate)          |
//| Recommended: EURUSD, USDCAD only.                                |
//| Not financial advice.                                             |
//+------------------------------------------------------------------+
#property strict
#property version "2.0"

#include <Trade/Trade.mqh>
CTrade trade;

//--- Mode selection
enum ENUM_STRATEGY_MODE
{
   MODE_MAX_R   = 0,   // Max R (555 trades, +189R, 86% WR)
   MODE_QUALITY = 1,   // Quality (306 trades, +123R, 92% WR)
   MODE_CUSTOM  = 2    // Custom (use manual filter inputs)
};

input ENUM_STRATEGY_MODE StrategyMode = MODE_MAX_R;  // Strategy Mode

//--- Basic parameters
input int    LookbackBars   = 20;
input int    EMA200Period   = 200;
input int    ATRPeriod      = 20;           // ATR Period for normalization
input double RiskPercent    = 0.5;          // % of equity risked per trade
input ulong  MagicNumber    = 503182618;
input double TP1_CloseFraction = 0.5;       // Close this fraction at TP1

//--- Break-Even Settings
input bool   UseBreakEven   = true;         // Move SL to entry after TP1
input double BreakEvenBuffer = 0.0;         // Buffer above entry (in points, 0 = exact entry)

//--- ATR Filter Settings (used when MODE_CUSTOM or to override)
input double RiskATR_Min    = 0.7;          // Min risk/ATR (filter too-tight stops)
input double RiskATR_Max    = 2.0;          // Max risk/ATR (filter too-wide stops)
input double ConfirmATR_Min = 0.1;          // Min confirm_strength/ATR
input double DepthATR_Min   = 0.1;          // Min zone_depth/ATR
input double SwingRangeATR_Max = 99.0;      // Max swing_range/ATR (99 = no limit)
input double MinR_TP1 = 0.15;               // Minimum R to TP1 (skip if TP1 too close/below entry)

//--- Symbol restriction
input bool   RestrictSymbols = false;       // Only trade EURUSD & USDCAD
input string AllowedSymbol1  = "EURUSD";
input string AllowedSymbol2  = "USDCAD";

//--- Trailing Stop (optional, for future)
input bool   UseTrailingStop = false;       // Enable ATR trailing stop
input double TrailATR_Multiple = 1.5;       // Trail distance in ATR units

// Internal state
int ema200Handle = INVALID_HANDLE;
int atrHandle    = INVALID_HANDLE;
datetime lastBarTime = 0;
bool tp1Hit = false;                        // Track if TP1 was hit for current position
double entryPrice = 0.0;                    // Store entry price for break-even
double currentTP1 = 0.0;                    // Store TP1 level

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber((int)MagicNumber);
   
   // Check symbol restriction
   if(RestrictSymbols)
   {
      string sym = _Symbol;
      if(StringFind(sym, AllowedSymbol1) < 0 && StringFind(sym, AllowedSymbol2) < 0)
      {
         Print("Symbol ", sym, " not in allowed list. EA disabled.");
         return(INIT_FAILED);
      }
   }

   ema200Handle = iMA(_Symbol, PERIOD_H1, EMA200Period, 0, MODE_EMA, PRICE_CLOSE);
   if(ema200Handle == INVALID_HANDLE) return(INIT_FAILED);

   atrHandle = iATR(_Symbol, PERIOD_H1, ATRPeriod);
   if(atrHandle == INVALID_HANDLE) return(INIT_FAILED);

   Print("FibPullbackEA v2.0 initialized. Mode: ", EnumToString(StrategyMode));
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(ema200Handle != INVALID_HANDLE) IndicatorRelease(ema200Handle);
   if(atrHandle    != INVALID_HANDLE) IndicatorRelease(atrHandle);
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
double GetATR(int shift)
{
   double buf[];
   if(CopyBuffer(atrHandle, 0, shift, 1, buf) != 1) return EMPTY_VALUE;
   return buf[0];
}

//+------------------------------------------------------------------+
void GetSwing(int lookback, int shiftEnd, double &swingLow, int &swingLowIdx, double &swingHigh)
{
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
// Get effective filter values based on mode
void GetFilterValues(double &riskMin, double &riskMax, double &confirmMin, double &depthMin, double &swingMax)
{
   switch(StrategyMode)
   {
      case MODE_MAX_R:
         riskMin = 0.7;
         riskMax = 2.0;
         confirmMin = 0.1;
         depthMin = 0.1;
         swingMax = 99.0;  // No swing filter
         break;
         
      case MODE_QUALITY:
         riskMin = 0.7;
         riskMax = 2.0;
         confirmMin = 0.25;
         depthMin = 0.1;
         swingMax = 2.0;
         break;
         
      case MODE_CUSTOM:
      default:
         riskMin = RiskATR_Min;
         riskMax = RiskATR_Max;
         confirmMin = ConfirmATR_Min;
         depthMin = DepthATR_Min;
         swingMax = SwingRangeATR_Max;
         break;
   }
}

//+------------------------------------------------------------------+
double CalcLotsFromRisk(double entry, double sl)
{
   double riskMoney = AccountInfoDouble(ACCOUNT_EQUITY) * (RiskPercent / 100.0);
   double dist = MathAbs(entry - sl);
   if(dist <= 0) return 0.0;

   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0 || tickValue <= 0) return 0.0;

   double valuePerPrice = tickValue / tickSize;
   double lots = riskMoney / (dist * valuePerPrice);

   double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;

   lots = MathFloor(lots / step) * step;
   return lots;
}

//+------------------------------------------------------------------+
void ManagePosition()
{
   if(!PositionSelect(_Symbol)) return;
   
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double posVol = PositionGetDouble(POSITION_VOLUME);
   double posSL = PositionGetDouble(POSITION_SL);
   double posTP = PositionGetDouble(POSITION_TP);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   double minStopDist = (double)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;

   // Check if TP1 hit (partial close)
   if(!tp1Hit && currentTP1 > 0 && bid >= currentTP1)
   {
      double volClose = posVol * TP1_CloseFraction;
      double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
      volClose = MathFloor(volClose / step) * step;
      
      if(volClose >= SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN))
      {
         if(trade.PositionClosePartial(_Symbol, volClose))
         {
            tp1Hit = true;
            Print("TP1 hit - closed ", volClose, " lots at ", bid);
         }
      }
   }

   // Break-Even management (retry on every tick after TP1, avoids one-shot invalid-stops failure)
   if(UseBreakEven && tp1Hit && entryPrice > 0)
   {
      double beTarget = entryPrice + BreakEvenBuffer * point;
      // broker rule for BUY SL: must be <= bid - minStopDist
      double maxAllowedSL = bid - minStopDist;
      double newSL = MathMin(beTarget, maxAllowedSL);
      newSL = NormalizeDouble(newSL, digits);

      // move only forward
      if(newSL > posSL + point)
      {
         if(trade.PositionModify(_Symbol, newSL, posTP))
         {
            Print("Break-Even activated/updated: SL moved to ", newSL);
         }
         else
         {
            Print("Break-Even modify failed, will retry. err=", GetLastError(),
                  " | bid=", bid, " | minStopDist=", minStopDist,
                  " | reqSL=", newSL, " | curSL=", posSL);
         }
      }
   }
   
   // Trailing stop (if enabled and after TP1)
   if(UseTrailingStop && tp1Hit)
   {
      double atr = GetATR(1);
      if(atr != EMPTY_VALUE)
      {
         double trailDist = atr * TrailATR_Multiple;
         double newSL = bid - trailDist;
         newSL = MathMin(newSL, bid - minStopDist);
         newSL = NormalizeDouble(newSL, digits);
         
         if(newSL > posSL + point)
         {
            trade.PositionModify(_Symbol, newSL, posTP);
         }
      }
   }
}

//+------------------------------------------------------------------+
void OnTick()
{
   // Always manage position (TP1 partial close, break-even, trailing)
   ManagePosition();
   
   // Entry logic only on new H1 bar
   if(!NewH1Bar()) return;

   // Only one position at a time
   if(PositionSelect(_Symbol)) return;

   // Reset tracking variables when no position
   tp1Hit = false;
   entryPrice = 0.0;
   currentTP1 = 0.0;

   int shift = 1;  // Last closed bar

   double ema200 = GetEMA(ema200Handle, shift);
   if(ema200 == EMPTY_VALUE) return;

   double atr = GetATR(shift);
   if(atr == EMPTY_VALUE || atr <= 0) return;

   double close1 = iClose(_Symbol, PERIOD_H1, shift);
   double low1   = iLow(_Symbol, PERIOD_H1, shift);
   double high1  = iHigh(_Symbol, PERIOD_H1, shift);

   // Trend filter: close above EMA200
   if(close1 <= ema200) return;

   // Swing detection
   double swingLow, swingHigh;
   int swingLowIdx;
   GetSwing(LookbackBars, shift, swingLow, swingLowIdx, swingHigh);
   if(swingHigh <= swingLow) return;

   double fib382 = swingHigh - 0.382 * (swingHigh - swingLow);
   double fib618 = swingHigh - 0.618 * (swingHigh - swingLow);

   // Calculate ATR-normalized features
   double risk = close1 - swingLow;              // Potential SL distance (entry~close, SL=swingLow)
   double confirm_strength = close1 - fib382;    // How far above fib382
   double zone_depth = MathMax(0, fib618 - low1);// How deep into zone
   double swing_range = swingHigh - swingLow;

   double risk_atr = risk / atr;
   double confirm_atr = confirm_strength / atr;
   double depth_atr = zone_depth / atr;
   double swing_range_atr = swing_range / atr;

   // Get filter values based on mode
   double f_riskMin, f_riskMax, f_confirmMin, f_depthMin, f_swingMax;
   GetFilterValues(f_riskMin, f_riskMax, f_confirmMin, f_depthMin, f_swingMax);

   // Apply ATR filters
   if(risk_atr < f_riskMin || risk_atr > f_riskMax)
   {
      // Print("Filtered: risk_atr=", risk_atr, " outside ", f_riskMin, "-", f_riskMax);
      return;
   }
   
   if(confirm_atr < f_confirmMin)
   {
      // Print("Filtered: confirm_atr=", confirm_atr, " < ", f_confirmMin);
      return;
   }
   
   if(depth_atr < f_depthMin)
   {
      // Print("Filtered: depth_atr=", depth_atr, " < ", f_depthMin);
      return;
   }
   
   if(swing_range_atr > f_swingMax)
   {
      // Print("Filtered: swing_range_atr=", swing_range_atr, " > ", f_swingMax);
      return;
   }

   // Entry conditions
   bool inZone = (low1 <= fib618);
   bool confirmed = (close1 > fib382);

   if(inZone && confirmed)
   {
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      double sl = swingLow;
      double tp1 = swingHigh;
      double tp2 = swingHigh + 0.618 * (swingHigh - swingLow);

      // Real-market guard: skip if TP1 is too close (or below) entry due spread/gap
      double riskReal = ask - sl;
      if(riskReal <= 0) return;
      double r_tp1 = (tp1 - ask) / riskReal;
      if(r_tp1 < MinR_TP1)
      {
         // Print("Skip entry: TP1 too close. r_tp1=", DoubleToString(r_tp1, 3));
         return;
      }

      double lots = CalcLotsFromRisk(ask, sl);
      if(lots <= 0) return;

      if(trade.Buy(lots, _Symbol, ask, sl, tp2, "FibPullback"))
      {
         entryPrice = ask;
         currentTP1 = tp1;
         Print("Entry: ", lots, " lots @ ", ask, 
               " | SL=", sl, " | TP1=", tp1, " | TP2=", tp2,
               " | Mode=", EnumToString(StrategyMode),
               " | risk_atr=", DoubleToString(risk_atr, 2),
               " | confirm_atr=", DoubleToString(confirm_atr, 2),
               " | r_tp1=", DoubleToString(r_tp1, 2));
      }
   }
}
//+------------------------------------------------------------------+

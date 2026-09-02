//+------------------------------------------------------------------+
//|                                         SpikeCatcher_v2.mq5      |
//|                              Optimized Counter-Move Strategy      |
//|                                                                    |
//| v2.04 - Fixed filling mode, invalid stops, and SL/TP ratio       |
//+------------------------------------------------------------------+
#property copyright "Genesis Trading System v2"
#property version   "2.05"
#property strict

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                   |
//+------------------------------------------------------------------+
input group "=== Trading System ==="
input int    TradingSystem  = 2;      // 1=Max Profit, 2=Max Safety

input group "=== Risk Management ==="
input double RiskPercent    = 2.0;    // Risk per trade (% of equity)
input double MaxLots        = 5.0;    // Maximum lot size

input group "=== Spike Detection (Optimized) ==="
input int    SpikeLookback  = 100;    // Ticks for median range calc
input double SpikeThreshold = 3.0;    // Range > Nx median = spike
input double MinSpikeSize   = 0.03;   // Min spike size (price) to trigger

input group "=== Entry (Optimized) ==="
input int    EntryBar       = 1;      // Enter at bar N after spike (1 = optimal)
input int    MaxHoldBars    = 5;      // Max bars to hold (timeout)

input group "=== TP/SL (Optimized) ==="
input double TPMultiplier   = 2.0;    // TP = Nx median range (2 = optimal)
input double SLMultiplier   = 1.0;    // SL = Nx median range (1 = optimal)

input group "=== Filters (Optimized) ==="
input int    ClusterWindow  = 10;     // Bars to check for clusters
input int    ClusterMin     = 3;      // Min spikes in window = skip
input bool   UseTrendFilter = true;   // Use M5 EMA trend filter
input int    EMA_Fast       = 10;     // Fast EMA period
input int    EMA_Slow       = 30;     // Slow EMA period

input group "=== Safety Filters (Max Safety Only) ==="
input bool   SkipDoubleSpikes = true; // Skip if previous spike was 1-3 bars ago
input int    DoubleSpikeWindow = 3;   // Bars to check for double spikes
input bool   UseColdHours    = true;  // Only trade during COLD hours
input int    ColdHoursStart  = 0;     // COLD hours start (0-23)
input int    ColdHoursEnd    = 6;     // COLD hours end (0-23)

input group "=== Safety Exit (CRITICAL) ==="
input bool   UseSafetyExit  = true;   // Close on spike detection
input double SafetyMoveMult = 2.5;    // Tick move > Nx median = spike (lower = faster)

input group "=== Order Execution ==="
input int    OrderDeviation = 500;    // Max price deviation (points) for entries

input group "=== Display ==="
input bool   ShowInfo       = true;   // Show info panel

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                   |
//+------------------------------------------------------------------+
double g_lastTickPrice = 0;
double g_tickMove = 0;
bool   g_spikeDetected = false;
int    g_spikeTickCount = 0;

double g_medianRange = 0;
double g_ranges[];
int    g_rangeCount = 0;
int    g_lastBarTime = 0;
int    g_barCount = 0;

int    g_spikes[];
int    g_spikeCount = 0;
int    g_spikeMax = 500;
int    g_lastSpikeBar = -999;
int    g_barsSinceSpike = 999;

bool   g_inPosition = false;
int    g_positionDir = 0;
double g_entryPrice = 0;
int    g_entryBar = 0;
int    g_barsHeld = 0;
ulong  g_positionTicket = 0;
bool   g_requiresNewSpike = true;

int    g_totalTrades = 0;
int    g_wins = 0;
int    g_losses = 0;
double g_totalPnL = 0;
int    g_safetyExits = 0;
int    g_spikesDetected = 0;

string g_logFile;
string g_configFile;

int g_hEmaFast = INVALID_HANDLE;
int g_hEmaSlow = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
{
   ArrayResize(g_ranges, SpikeLookback);
   ArrayInitialize(g_ranges, 0);
   ArrayResize(g_spikes, g_spikeMax);

   InitFiles();

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;

      g_inPosition = true;
      g_positionDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
      g_entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      g_positionTicket = ticket;
      g_barsHeld = 0;
      g_entryBar = g_barCount;
      Print("[RESTART] Restored position: ", (g_positionDir==1?"LONG":"SHORT"),
            " @ ", DoubleToString(g_entryPrice, 3));
      break;
   }

   g_medianRange = 0.01;

   // Apply system presets
   if(TradingSystem == 1) {
      // MAX PROFIT: Delay 1, Exit 5, no extra filters
      EntryBar = 1;
      MaxHoldBars = 5;
      SkipDoubleSpikes = false;
      UseColdHours = false;
      Print("=== SYSTEM: MAX PROFIT ===");
   } else {
      // MAX SAFETY: Delay 1, Exit 3, all defenses
      EntryBar = 1;
      MaxHoldBars = 3;
      SkipDoubleSpikes = true;
      UseColdHours = true;
      Print("=== SYSTEM: MAX SAFETY ===");
   }

   Print("=== SpikeCatcher v2.05 ===");
   Print("Entry: Bar ", EntryBar, " | TP: ", TPMultiplier, "x | SL: ", SLMultiplier, "x");
   Print("Safety: ", UseSafetyExit ? "ON" : "OFF", " | Threshold: ", SafetyMoveMult, "x");
   Print("Cluster: skip if ", ClusterMin, "+ in ", ClusterWindow, " bars");
   Print("Double Spike: ", SkipDoubleSpikes ? "ON" : "OFF", " | Cold Hours: ", UseColdHours ? "ON" : "OFF");

   int rawFilling = (int)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);
   ENUM_ORDER_TYPE_FILLING chosen = GetFillingMode();
   string chosenStr = (chosen == ORDER_FILLING_FOK) ? "FOK" :
                       (chosen == ORDER_FILLING_IOC) ? "IOC" : "RETURN";
   Print("[FILLING] Symbol raw mode bitmask=", rawFilling,
         " (FOK bit=", (rawFilling & SYMBOL_FILLING_FOK) != 0,
         ", IOC bit=", (rawFilling & SYMBOL_FILLING_IOC) != 0,
         ") -> using ", chosenStr);

   long stopsLevel  = SymbolInfoInteger(Symbol(), SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevel = SymbolInfoInteger(Symbol(), SYMBOL_TRADE_FREEZE_LEVEL);
   double minStopDist = GetMinStopDistance();
   Print("[STOPS] StopsLevel=", stopsLevel, " pts FreezeLevel=", freezeLevel,
         " pts -> min SL/TP distance=", DoubleToString(minStopDist, 5),
         " (current median range=", DoubleToString(g_medianRange, 5), ")");

   if(UseTrendFilter)
   {
      g_hEmaFast = iMA(Symbol(), PERIOD_M5, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
      g_hEmaSlow = iMA(Symbol(), PERIOD_M5, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   }

   EventSetMillisecondTimer(50);

   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   CleanupPanel();

   if(g_hEmaFast != INVALID_HANDLE) IndicatorRelease(g_hEmaFast);
   if(g_hEmaSlow != INVALID_HANDLE) IndicatorRelease(g_hEmaSlow);

   double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;
   Print("=== Final: ", g_totalTrades, " trades, WR=", DoubleToString(wr,1), "%, ",
         "PnL=$", DoubleToString(g_totalPnL, 2),
         " SafetyExits=", g_safetyExits, " ===");
}

//+------------------------------------------------------------------+
//| Pick a filling mode the symbol actually supports                   |
//+------------------------------------------------------------------+
ENUM_ORDER_TYPE_FILLING GetFillingMode()
{
   int filling = (int)SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);

   if((filling & SYMBOL_FILLING_FOK) != 0)
      return ORDER_FILLING_FOK;
   if((filling & SYMBOL_FILLING_IOC) != 0)
      return ORDER_FILLING_IOC;

   return ORDER_FILLING_FOK;
}

//+------------------------------------------------------------------+
//| Minimum SL/TP distance the broker will accept                     |
//+------------------------------------------------------------------+
double GetMinStopDistance()
{
   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   long stopsLevel  = SymbolInfoInteger(Symbol(), SYMBOL_TRADE_STOPS_LEVEL);
   long freezeLevel = SymbolInfoInteger(Symbol(), SYMBOL_TRADE_FREEZE_LEVEL);
   long minLevelPts = MathMax(stopsLevel, freezeLevel);

   double minDist = minLevelPts * point;

   long spreadPts = SymbolInfoInteger(Symbol(), SYMBOL_SPREAD);
   double spreadDist = spreadPts * point;

   double floorDist = MathMax(minDist, spreadDist);

   return floorDist * 1.5;
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   if(price <= 0) return;

   if(g_lastTickPrice > 0 && g_medianRange > 0)
   {
      g_tickMove = MathAbs(price - g_lastTickPrice);

      if(g_tickMove > SafetyMoveMult * g_medianRange)
      {
         g_spikesDetected++;

         if(UseSafetyExit && g_inPosition)
         {
            bool against = false;
            if(g_positionDir == 1 && price < g_entryPrice) against = true;
            if(g_positionDir == -1 && price > g_entryPrice) against = true;

            if(against)
            {
               double pnl = CalculateCurrentPnL(price);
               ClosePosition("safety_spike", pnl);
               g_safetyExits++;
            }
         }
      }
   }
   g_lastTickPrice = price;

   MqlRates rates[];
   if(CopyRates(Symbol(), PERIOD_M1, 0, 3, rates) < 3) return;

   int currentBarTime = (int)rates[0].time;
   if(currentBarTime != g_lastBarTime)
   {
      OnNewBar(rates);
      g_lastBarTime = currentBarTime;
      g_barCount++;
   }
}

//+------------------------------------------------------------------+
//| New bar processing                                                 |
//+------------------------------------------------------------------+
void OnNewBar(MqlRates &rates[])
{
   double o = rates[1].open;
   double h = rates[1].high;
   double l = rates[1].low;
   double c = rates[1].close;
   double barRange = h - l;

   UpdateMedianRange(barRange);

   bool isSpike = (barRange > SpikeThreshold * g_medianRange);
   bool isCrashSpike = isSpike && (c < o);
   bool isBoomSpike = isSpike && (c > o);
   bool isMySpike = IsCrash() ? isCrashSpike : isBoomSpike;

   if(isMySpike && g_medianRange > 0)
   {
      g_lastSpikeBar = g_barCount;
      g_barsSinceSpike = 0;
      g_requiresNewSpike = false;
      g_spikes[g_spikeCount % g_spikeMax] = g_barCount;
      g_spikeCount++;
      Print("[SPIKE #", g_spikeCount, "] Bar ", g_barCount,
            " Range=", DoubleToString(barRange,5),
            " Median=", DoubleToString(g_medianRange,5),
            " Ratio=", DoubleToString(barRange/g_medianRange,1), "x");

      // DOUBLE SPIKE CHECK: if we're in a position and spike is against us, exit immediately
      if(g_inPosition && UseSafetyExit)
      {
         bool against = false;
         if(g_positionDir == 1 && c < o) against = true;  // Long, crash spike
         if(g_positionDir == -1 && c > o) against = true; // Short, boom spike
         if(against)
         {
            double pnl = CalculateCurrentPnL(c);
            ClosePosition("double_spike", pnl);
            g_safetyExits++;
         }
      }
   }
   else
   {
      g_barsSinceSpike++;
   }

   if(g_inPosition)
   {
      g_barsHeld++;

      if(CheckExitLevels(rates))
         return;

      if(g_barsHeld >= MaxHoldBars)
      {
         double pnl = CalculateCurrentPnL(c);
         ClosePosition("timeout", pnl);
      }
   }
   else
   {
      if(!g_inPosition && g_barsSinceSpike == EntryBar)
         CheckEntry();
   }

   if(ShowInfo) UpdatePanel();
}

//+------------------------------------------------------------------+
//| Update median range                                                |
//+------------------------------------------------------------------+
void UpdateMedianRange(double newRange)
{
   g_ranges[g_rangeCount % SpikeLookback] = newRange;
   g_rangeCount++;

   int count = MathMin(g_rangeCount, SpikeLookback);
   double temp[];
   ArrayResize(temp, count);
   for(int i = 0; i < count; i++)
      temp[i] = g_ranges[i];
   ArraySort(temp);
   g_medianRange = temp[count / 2];
}

//+------------------------------------------------------------------+
//| Check entry after spike                                            |
//+------------------------------------------------------------------+
void CheckEntry()
{
   if(g_inPosition) return;
   if(g_barsSinceSpike != EntryBar) return;
   if(g_lastSpikeBar < 0) return;
   if(g_medianRange <= 0) return;

   // SKIP DOUBLE SPIKES: if previous spike was only 1-3 bars ago, skip
   if(SkipDoubleSpikes && g_spikeCount >= 2)
   {
      int prevSpikeBar = g_spikes[(g_spikeCount - 2) % g_spikeMax];
      int interval = g_lastSpikeBar - prevSpikeBar;
      if(interval >= 1 && interval <= DoubleSpikeWindow)
      {
         Print("[SKIP] Double spike risk: interval=", interval, " bars");
         return;
      }
   }

   // COLD HOURS: only trade during low-spike hours
   if(UseColdHours)
   {
      MqlDateTime dt;
      TimeCurrent(dt);
      int hour = dt.hour;
      bool inCold = false;
      if(ColdHoursStart < ColdHoursEnd)
         inCold = (hour >= ColdHoursStart && hour < ColdHoursEnd);
      else // wraps midnight
         inCold = (hour >= ColdHoursStart || hour < ColdHoursEnd);
      if(!inCold)
      {
         Print("[SKIP] Not COLD hour: ", hour);
         return;
      }
   }

   int clusterCount = 0;
   for(int i = 0; i < g_spikeCount; i++)
   {
      int spikeBar = g_spikes[i];
      if(g_barCount - spikeBar <= ClusterWindow && spikeBar != g_lastSpikeBar)
         clusterCount++;
   }
   if(clusterCount >= ClusterMin - 1) return;

   if(UseTrendFilter && !CheckTrend()) return;

   if(g_requiresNewSpike) return;

   int dir = IsCrash() ? 1 : -1;

   double sl = SLMultiplier * g_medianRange;
   double tp = TPMultiplier * g_medianRange;

   double minStopDist = GetMinStopDistance();
   if(sl < minStopDist)
   {
      double ratio = TPMultiplier / SLMultiplier;
      Print("[STOPS] Median-based SL=", DoubleToString(sl,5),
            " below broker minimum=", DoubleToString(minStopDist,5),
            " — flooring SL to minimum, scaling TP to preserve ",
            DoubleToString(ratio,2), ":1 ratio");
      sl = minStopDist;
      tp = sl * ratio;
   }

   double lotSize = CalculateLotSize(sl);
   if(lotSize <= 0) {
      Print("[SKIP] Lot size too small. SL=", DoubleToString(sl,5),
            " Median=", DoubleToString(g_medianRange,5),
            " Equity=$", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2));
      return;
   }

   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

   bool success = false;
   if(dir == 1)
      success = OpenBuy(lotSize, tp, sl);
   else
      success = OpenSell(lotSize, tp, sl);

   if(success)
   {
      g_inPosition = true;
      g_requiresNewSpike = true;
      g_positionDir = dir;
      g_entryPrice = dir == 1 ?
         SymbolInfoDouble(Symbol(), SYMBOL_ASK) :
         SymbolInfoDouble(Symbol(), SYMBOL_BID);
      g_entryBar = g_barCount;
      g_barsHeld = 0;

      Print("[ENTRY] ", (dir == 1 ? "LONG" : "SHORT"),
            " @ ", DoubleToString(g_entryPrice, digits),
            " TP=", DoubleToString(tp, digits),
            " SL=", DoubleToString(sl, digits),
            " Lots=", DoubleToString(lotSize, 2));

      LogTrade("OPEN", (dir == 1 ? "LONG" : "SHORT"), g_entryPrice, 0, 0, "entry");

      if(!AttachStops(dir, tp, sl))
      {
         Print("[NO STOPS] Could not attach SL/TP after entry — closing for safety");
         double exitPrice = (dir == 1) ?
            SymbolInfoDouble(Symbol(), SYMBOL_BID) :
            SymbolInfoDouble(Symbol(), SYMBOL_ASK);
         double pnl = CalculateCurrentPnL(exitPrice);
         ClosePosition("no_stops", pnl);
      }
   }
}

//+------------------------------------------------------------------+
//| Check TP/SL hit                                                    |
//+------------------------------------------------------------------+
bool CheckExitLevels(MqlRates &rates[])
{
   if(!g_inPosition) return false;

   double h = rates[0].high;
   double l = rates[0].low;
   double tp = TPMultiplier * g_medianRange;
   double sl = SLMultiplier * g_medianRange;

   bool hitTP = false;
   bool hitSL = false;

   if(g_positionDir == 1)
   {
      hitTP = (h - g_entryPrice) >= tp;
      hitSL = (g_entryPrice - l) >= sl;
   }
   else
   {
      hitTP = (g_entryPrice - l) >= tp;
      hitSL = (h - g_entryPrice) >= sl;
   }

   if(hitTP)
   {
      double tickVal = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
      double tickSz = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
      double lots = PositionGetDouble(POSITION_VOLUME);
      double pnl = (tp * tickVal / tickSz) * lots;
      ClosePosition("tp", pnl);
      return true;
   }

   if(hitSL)
   {
      double tickVal = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
      double tickSz = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
      double lots = PositionGetDouble(POSITION_VOLUME);
      double pnl = -(sl * tickVal / tickSz) * lots;
      ClosePosition("sl", pnl);
      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| Calculate current PnL                                              |
//+------------------------------------------------------------------+
double CalculateCurrentPnL(double currentPrice)
{
   if(!g_inPosition) return 0;
   double diff = (g_positionDir == 1) ? (currentPrice - g_entryPrice) : (g_entryPrice - currentPrice);
   double tickValue = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   double lots = PositionGetDouble(POSITION_VOLUME);
   if(tickSize <= 0) return 0;
   return diff * tickValue / tickSize * lots;
}

//+------------------------------------------------------------------+
//| Check M5 trend filter                                              |
//+------------------------------------------------------------------+
bool CheckTrend()
{
   if(g_hEmaFast == INVALID_HANDLE || g_hEmaSlow == INVALID_HANDLE) return true;

   double emaFast[], emaSlow[];
   ArrayResize(emaFast, 1);
   ArrayResize(emaSlow, 1);

   if(CopyBuffer(g_hEmaFast, 0, 0, 1, emaFast) < 1) return true;
   if(CopyBuffer(g_hEmaSlow, 0, 0, 1, emaSlow) < 1) return true;

   if(IsCrash())
      return emaFast[0] > emaSlow[0];
   else
      return emaFast[0] < emaSlow[0];
}

//+------------------------------------------------------------------+
//| Calculate lot size                                                  |
//+------------------------------------------------------------------+
double CalculateLotSize(double slDistance)
{
   if(slDistance <= 0) return 0;

   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskAmount = equity * RiskPercent / 100.0;

   double tickValue = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);

   if(tickValue <= 0 || tickSize <= 0) return SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);

   double valuePerPoint = tickValue * (point / tickSize);
   double lots = riskAmount / (slDistance * valuePerPoint);

   double minLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MAX);
   double lotStep = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);

   lots = MathFloor(lots / lotStep) * lotStep;
   lots = MathMax(lots, minLot);
   lots = MathMin(lots, MathMin(maxLot, MaxLots));

   return lots;
}

//+------------------------------------------------------------------+
//| Open buy order                                                     |
//+------------------------------------------------------------------+
bool OpenBuy(double lots, double tp, double sl)
{
   MqlTradeRequest request = {};
   MqlTradeResult result = {};

   int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);

   request.action = TRADE_ACTION_DEAL;
   request.symbol = Symbol();
   request.volume = lots;
   request.type = ORDER_TYPE_BUY;
   request.price = NormalizeDouble(ask, digits);
   request.deviation = OrderDeviation;
   request.magic = 202603;
   request.comment = "SC2_BUY";
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = GetFillingMode();
   request.sl = 0;
   request.tp = 0;

   bool sent = OrderSend(request, result);
   if(!sent || result.retcode != TRADE_RETCODE_DONE)
   {
      Print("[BUY FAIL] Error: ", result.retcode, " ", result.comment);
      return false;
   }

   g_positionTicket = result.order;
   return true;
}

//+------------------------------------------------------------------+
//| Open sell order                                                    |
//+------------------------------------------------------------------+
bool OpenSell(double lots, double tp, double sl)
{
   MqlTradeRequest request = {};
   MqlTradeResult result = {};

   int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
   double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);

   request.action = TRADE_ACTION_DEAL;
   request.symbol = Symbol();
   request.volume = lots;
   request.type = ORDER_TYPE_SELL;
   request.price = NormalizeDouble(bid, digits);
   request.deviation = OrderDeviation;
   request.magic = 202603;
   request.comment = "SC2_SELL";
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = GetFillingMode();
   request.sl = 0;
   request.tp = 0;

   bool sent = OrderSend(request, result);
   if(!sent || result.retcode != TRADE_RETCODE_DONE)
   {
      Print("[SELL FAIL] Error: ", result.retcode, " ", result.comment);
      return false;
   }

   g_positionTicket = result.order;
   return true;
}

//+------------------------------------------------------------------+
//| Attach SL/TP to just-opened position                              |
//+------------------------------------------------------------------+
bool AttachStops(int dir, double tpDist, double slDist)
{
   ulong ticket = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
      ticket = t;
      break;
   }
   if(ticket == 0)
   {
      Print("[SLTP] No open position found to attach stops to");
      return false;
   }

   for(int attempt = 0; attempt < 2; attempt++)
   {
      double mult = (attempt == 0) ? 1.0 : 1.5;

      if(!PositionSelectByTicket(ticket)) return false;
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

      double slPrice, tpPrice;
      if(dir == 1) { slPrice = openPrice - slDist * mult; tpPrice = openPrice + tpDist * mult; }
      else         { slPrice = openPrice + slDist * mult; tpPrice = openPrice - tpDist * mult; }

      MqlTradeRequest request = {};
      MqlTradeResult result = {};
      request.action   = TRADE_ACTION_SLTP;
      request.position = ticket;
      request.symbol   = Symbol();
      request.sl       = NormalizeDouble(slPrice, digits);
      request.tp       = NormalizeDouble(tpPrice, digits);

      bool sent = OrderSend(request, result);
      if(sent && result.retcode == TRADE_RETCODE_DONE)
      {
         Print("[SLTP] Attached SL=", DoubleToString(request.sl, digits),
               " TP=", DoubleToString(request.tp, digits),
               " open=", DoubleToString(openPrice, digits),
               " (attempt ", attempt+1, ", ", DoubleToString(mult,1), "x)");
         return true;
      }

      Print("[SLTP FAIL] attempt ", attempt+1, " Error: ", result.retcode, " ", result.comment,
            " openPrice=", DoubleToString(openPrice, digits),
            " sl=", DoubleToString(request.sl, digits),
            " tp=", DoubleToString(request.tp, digits));
   }

   return false;
}

//+------------------------------------------------------------------+
//| Close position                                                     |
//+------------------------------------------------------------------+
bool ClosePosition(string reason, double pnl)
{
   if(!g_inPosition) return false;

   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;

      MqlTradeRequest request = {};
      MqlTradeResult result = {};

      int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

      request.action = TRADE_ACTION_DEAL;
      request.symbol = Symbol();
      request.volume = PositionGetDouble(POSITION_VOLUME);
      request.position = ticket;
      request.deviation = 500;
      request.magic = 202603;
      request.comment = "SC2_" + reason;
      request.type_time = ORDER_TIME_GTC;
      request.type_filling = GetFillingMode();

      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
      {
         request.type = ORDER_TYPE_SELL;
         request.price = NormalizeDouble(SymbolInfoDouble(Symbol(), SYMBOL_BID), digits);
      }
      else
      {
         request.type = ORDER_TYPE_BUY;
         request.price = NormalizeDouble(SymbolInfoDouble(Symbol(), SYMBOL_ASK), digits);
      }

      bool sent = OrderSend(request, result);
      if(!sent || result.retcode != TRADE_RETCODE_DONE)
      {
         Print("[CLOSE FAIL] ", reason, " Error: ", result.retcode);
         return false;
      }

      g_totalTrades++;
      g_totalPnL += pnl;
      if(pnl > 0) g_wins++; else g_losses++;

      double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;
      Print("[", reason, "] PnL=$", DoubleToString(pnl, 4),
            " | ", g_totalTrades, "T WR=", DoubleToString(wr, 1), "%",
            " PnL=$", DoubleToString(g_totalPnL, 4),
            " Safety=", g_safetyExits);

      double exitPrice = (g_positionDir == 1) ?
         SymbolInfoDouble(Symbol(), SYMBOL_BID) :
         SymbolInfoDouble(Symbol(), SYMBOL_ASK);
      LogTrade("CLOSE", (g_positionDir == 1 ? "LONG" : "SHORT"),
               g_entryPrice, exitPrice, pnl, reason);

      g_inPosition = false;
      g_requiresNewSpike = true;
      g_positionDir = 0;
      g_entryPrice = 0;
      g_barsHeld = 0;
      g_positionTicket = 0;

      return true;
   }

   return false;
}

//+------------------------------------------------------------------+
//| Helper functions                                                   |
//+------------------------------------------------------------------+
bool IsCrash()
{
   string name = Symbol();
   StringToLower(name);
   return StringFind(name, "crash") >= 0;
}

//+------------------------------------------------------------------+
//| FILE I/O                                                           |
//+------------------------------------------------------------------+
void InitFiles()
{
   string folder = "SpikeCatcher";
   g_logFile = folder + "\\trades_" + Symbol() + ".csv";
   g_configFile = folder + "\\config_" + Symbol() + ".json";

   if(!FileIsExist(g_logFile, FILE_COMMON))
   {
      int h = FileOpen(g_logFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
      if(h != INVALID_HANDLE)
      {
         FileWrite(h, "time","symbol","action","direction","entry","exit",
                   "pnl","reason","bars_held","equity","tp_mult","sl_mult");
         FileClose(h);
      }
   }
}

void LogTrade(string action, string direction, double entry, double exit,
              double pnl, string reason)
{
   int h = FileOpen(g_logFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);

   string time = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
   FileWrite(h, time, Symbol(), action, direction,
             DoubleToString(entry, 5), DoubleToString(exit, 5),
             DoubleToString(pnl, 4), reason, g_barsHeld,
             DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2),
             DoubleToString(TPMultiplier, 2), DoubleToString(SLMultiplier, 2));
   FileClose(h);
}

//+------------------------------------------------------------------+
//| Chart panel                                                        |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(ShowInfo) UpdatePanel();
}

void UpdatePanel()
{
   string p = "SC2_";
   int x = 10, y = 30;

   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;

   string sysName = (TradingSystem == 1) ? "MAX PROFIT" : "MAX SAFETY";
   color sysClr = (TradingSystem == 1) ? clrLime : clrYellow;
   CreateLabel(p+"t1", x, y, "SPIKE CATCHER v2.05 [" + sysName + "] — " + Symbol(), sysClr, 10); y+=20;
   CreateLabel(p+"t2", x, y, "Price: "+DoubleToString(price,3)+" Equity: $"+DoubleToString(equity,2), clrWhite, 9); y+=18;
   CreateLabel(p+"t3", x, y, "Median: "+DoubleToString(g_medianRange,4)+" Bars: "+IntegerToString(g_barCount), clrWhite, 9); y+=18;

   string posStr = "FLAT";
   color posClr = clrWhite;
   if(g_inPosition)
   {
      posStr = (g_positionDir==1?"LONG":"SHORT")+" @ "+DoubleToString(g_entryPrice,3)+" ("+IntegerToString(g_barsHeld)+" bars)";
      posClr = g_positionDir==1 ? clrLime : clrRed;
   }
   CreateLabel(p+"t4", x, y, "Pos: "+posStr, posClr, 9); y+=18;
   CreateLabel(p+"t5", x, y, "Trades: "+IntegerToString(g_totalTrades)+" WR: "+DoubleToString(wr,1)+"% PnL: $"+DoubleToString(g_totalPnL,4), clrWhite, 9); y+=18;
   CreateLabel(p+"t6", x, y, "Safety Exits: "+IntegerToString(g_safetyExits)+" Spikes Detected: "+IntegerToString(g_spikesDetected), clrYellow, 9);
}

void CreateLabel(string name, int x, int y, string text, color clr, int sz)
{
   if(ObjectFind(0, name) < 0)
   {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   }
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, sz);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
}

void CleanupPanel()
{
   ObjectsDeleteAll(0, "SC2_");
}
//+------------------------------------------------------------------+

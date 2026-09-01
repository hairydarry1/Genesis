//+------------------------------------------------------------------+
//|                                         SpikeCatcher_v2.mq5      |
//|                              Optimized Counter-Move Strategy      |
//|                                                                    |
//| OPTIMAL SETTINGS (from backtest):                                 |
//|   Entry: Bar 2 after spike                                        |
//|   TP: 2x median range | SL: 1x median range                      |
//|   Cluster filter: Skip if 3+ spikes in 10 bars                   |
//|   Trend filter: M5 EMA 10/30                                     |
//|   Safety exit: Close on new spike detection (FASTEST)             |
//|                                                                    |
//| SAFETY EXIT is the KEY edge:                                      |
//|   Without: Full spike damage (5-18x SL slippage)                  |
//|   With: Tick 1 damage only (31-38% less damage)                   |
//|                                                                    |
//| Works on: Crash 300, Crash 500, Boom 300, Boom 500               |
//+------------------------------------------------------------------+
#property copyright "Genesis Trading System v2"
#property version   "2.00"
#property strict

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                   |
//+------------------------------------------------------------------+
input group "=== Risk Management ==="
input double RiskPercent    = 2.0;    // Risk per trade (% of equity)
input double MaxLots        = 5.0;    // Maximum lot size

input group "=== Spike Detection (Optimized) ==="
input int    SpikeLookback  = 100;    // Ticks for median range calc
input double SpikeThreshold = 3.0;    // Range > Nx median = spike
input double MinSpikeSize   = 0.03;   // Min spike size (price) to trigger

input group "=== Entry (Optimized) ==="
input int    EntryBar       = 2;      // Enter at bar N after spike (2 = optimal)
input int    MaxHoldBars    = 6;      // Max bars to hold (timeout=6 = optimal)

input group "=== TP/SL (Optimized) ==="
input double TPMultiplier   = 2.0;    // TP = Nx median range (2 = optimal)
input double SLMultiplier   = 1.0;    // SL = Nx median range (1 = optimal)

input group "=== Filters (Optimized) ==="
input int    ClusterWindow  = 10;     // Bars to check for clusters
input int    ClusterMin     = 3;      // Min spikes in window = skip
input bool   UseTrendFilter = true;   // Use M5 EMA trend filter
input int    EMA_Fast       = 10;     // Fast EMA period
input int    EMA_Slow       = 30;     // Slow EMA period

input group "=== COLD Hours Filter (PROVEN) ==="
input bool   UseColdHours   = true;   // Only trade during COLD hours
input string ColdHours_BOOM500  = "1,5,12,14,15,19";   // BOOM500 safest hours
input string ColdHours_CRASH500 = "0,6,7,9,22,23";     // CRASH500 safest hours
input string ColdHours_BOOM1000 = "6,13,18,20,21,22";  // BOOM1000 safest hours
input string ColdHours_CRASH1000 = "3,6,7,18,22,23";   // CRASH1000 safest hours

input group "=== Safety Exit (CRITICAL) ==="
input bool   UseSafetyExit  = true;   // Close on spike detection
input double SafetyMoveMult = 2.5;    // Tick move > Nx median = spike (lower = faster)

input group "=== Velocity Exit (PRE-SPIKE) ==="
input bool   UseVelocityExit = false; // Velocity exit (OFF = proven not to work)
input int    VelocityThreshold = 12;  // Consecutive buildup ticks to trigger exit

input group "=== Display ==="
input bool   ShowInfo       = true;   // Show info panel

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                   |
//+------------------------------------------------------------------+
// Auto-detected filling mode for broker compatibility
ENUM_ORDER_TYPE_FILLING g_fillingMode = ORDER_FILLING_FOK;

// Tick-level tracking (FASTEST)
double g_lastTickPrice = 0;
double g_tickMove = 0;
bool   g_spikeDetected = false;      // Spike detected THIS tick
int    g_spikeTickCount = 0;         // Ticks since spike started

// Velocity tracking (PRE-SPIKE DETECTION)
int    g_consecutiveBuildup = 0;     // Consecutive buildup ticks (CRASH=uptick, BOOM=downtick)
int    g_velocityExits = 0;          // Count of velocity-triggered exits
double g_velocityPnL = 0;            // PnL saved by velocity exits
double g_velocityDamageSaved = 0;    // Damage avoided (entry vs exit price)
string g_velocityLogFile;            // Velocity exit log file

// COLD hours tracking
int    g_coldHours[];                 // Array of allowed trading hours
int    g_coldCount = 0;              // Number of COLD hours
bool   g_isColdHour = false;         // Current hour is COLD (safe to trade)
int    g_blockedByCold = 0;           // Trades skipped due to COLD filter

// Bar-level tracking
double g_medianRange = 0;
double g_ranges[];
int    g_rangeCount = 0;
int    g_lastBarTime = 0;
int    g_barCount = 0;

// Spike tracking
int    g_spikes[];       // Stores bar indices of spikes
int    g_spikeCount = 0;
int    g_spikeMax = 500;  // Store up to 500 spikes for cluster filter
int    g_lastSpikeBar = -999;
int    g_barsSinceSpike = 999;

// Position state
bool   g_inPosition = false;
int    g_positionDir = 0;       // 1=long, -1=short
double g_entryPrice = 0;
int    g_entryBar = 0;
int    g_barsHeld = 0;
ulong  g_positionTicket = 0;
bool   g_requiresNewSpike = true;  // Must see new spike before entering

// Performance
int    g_totalTrades = 0;
int    g_wins = 0;
int    g_losses = 0;
double g_totalPnL = 0;
int    g_safetyExits = 0;
int    g_spikesDetected = 0;

// File I/O
string g_logFile;
string g_configFile;

//+------------------------------------------------------------------+
//| Expert initialization                                              |
//+------------------------------------------------------------------+
int OnInit()
{
   ArrayResize(g_ranges, SpikeLookback);
   ArrayInitialize(g_ranges, 0);
   ArrayResize(g_spikes, g_spikeMax);
   
   InitFiles();
   
   // Detect existing positions on restart (prevent double entries)
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
      if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
      
      // Found existing position — restore state
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
   
   // Auto-detect filling mode for broker
   DetectFillingMode();
   
   // Initialize COLD hours based on symbol
   InitColdHours();
   
   // Initialize median range with reasonable default (0.01) to avoid false spikes
   g_medianRange = 0.01;
   
   Print("=== SpikeCatcher v2 ===");
   Print("Entry: Bar ", EntryBar, " | TP: ", TPMultiplier, "x | SL: ", SLMultiplier, "x");
   Print("Safety: ", UseSafetyExit ? "ON" : "OFF", " | Threshold: ", SafetyMoveMult, "x");
   Print("Velocity: ", UseVelocityExit ? "ON" : "OFF", " | Threshold: ", VelocityThreshold, " ticks");
   Print("COLD Hours: ", UseColdHours ? "ON" : "OFF", " | Hours: ", GetColdHoursString());
   Print("MaxHold: ", MaxHoldBars, " bars");
   Print("Cluster: skip if ", ClusterMin, "+ in ", ClusterWindow, " bars");
   Print("Filling: ", EnumToString(g_fillingMode));
   
   // Create indicator handles once
   if(UseTrendFilter)
   {
      g_hEmaFast = iMA(Symbol(), PERIOD_M5, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
      g_hEmaSlow = iMA(Symbol(), PERIOD_M5, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   }
   
   EventSetMillisecondTimer(50);  // 50ms timer for chart updates
   
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   CleanupPanel();
   
   // Release indicator handles
   if(g_hEmaFast != INVALID_HANDLE) IndicatorRelease(g_hEmaFast);
   if(g_hEmaSlow != INVALID_HANDLE) IndicatorRelease(g_hEmaSlow);
   
   double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;
   Print("=== Final: ", g_totalTrades, " trades, WR=", DoubleToString(wr,1), "%, ",
         "PnL=$", DoubleToString(g_totalPnL, 2),
         " SafetyExits=", g_safetyExits, " VelocityExits=", g_velocityExits,
         " ColdBlocked=", g_blockedByCold, " ===");
}

//+------------------------------------------------------------------+
//| Expert tick function — THE FAST PATH                               |
//| Called on EVERY tick (~1000/sec)                                   |
//| This is where safety exit fires                                   |
//+------------------------------------------------------------------+
void OnTick()
{
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   if(price <= 0) return;
   
   // ═══════════════════════════════════════════════════════════════
   // SPIKE DETECTION — Check every tick (FASTEST)
   // ═══════════════════════════════════════════════════════════════
   if(g_lastTickPrice > 0 && g_medianRange > 0)
   {
      g_tickMove = MathAbs(price - g_lastTickPrice);
      
      // SPIKE DETECTED: tick move > threshold
      if(g_tickMove > SafetyMoveMult * g_medianRange)
      {
         g_spikesDetected++;
         
         // ═══════════════════════════════════════════════════════
         // SAFETY EXIT — CLOSE IMMEDIATELY (KEY EDGE)
         // ═══════════════════════════════════════════════════════
         if(UseSafetyExit && g_inPosition)
         {
            // Check if spike is AGAINST our position
            bool against = false;
            if(g_positionDir == 1 && price < g_entryPrice) against = true;  // Long, spike down
            if(g_positionDir == -1 && price > g_entryPrice) against = true; // Short, spike up
            
            if(against)
            {
               // CLOSE IMMEDIATELY — take tick 1 damage only
               double pnl = CalculateCurrentPnL(price);
               ClosePosition("safety_spike", pnl);
               g_safetyExits++;
            }
         }
         
         // Reset buildup counter on spike
         g_consecutiveBuildup = 0;
      }
      else
      {
         // ═══════════════════════════════════════════════════════
         // VELOCITY TRACKING — Count buildup ticks
         // ═══════════════════════════════════════════════════════
         // CRASH: buildup = upticks (price climbing before crash)
         // BOOM:  buildup = downticks (price falling before boom)
         // Both: tick moving in SPIKE direction = buildup
         double tickChange = price - g_lastTickPrice;
         bool isCrash = IsCrash();
         
         if(isCrash)
         {
            // CRASH spike goes DOWN. Buildup = upticks (price climbing)
            if(tickChange > 0)
               g_consecutiveBuildup++;
            else if(tickChange < 0)
               g_consecutiveBuildup = 0;
         }
         else
         {
            // BOOM spike goes UP. Buildup = downticks (price falling)
            if(tickChange < 0)
               g_consecutiveBuildup++;
            else if(tickChange > 0)
               g_consecutiveBuildup = 0;
         }
         
         // ═══════════════════════════════════════════════════════
         // VELOCITY EXIT — Close BEFORE spike (PRE-SPIKE EDGE)
         // ═══════════════════════════════════════════════════════
         if(UseVelocityExit && g_inPosition && g_consecutiveBuildup >= VelocityThreshold)
         {
            // Buildup exceeded threshold — spike is imminent
            double pnl = CalculateCurrentPnL(price);
            ClosePosition("velocity_exit", pnl);
            g_velocityExits++;
            g_consecutiveBuildup = 0;
         }
      }
   }
   g_lastTickPrice = price;
   
   // ═══════════════════════════════════════════════════════════════
   // NEW BAR CHECK — Bar-level logic
   // ═══════════════════════════════════════════════════════════════
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
//| New bar processing — runs once per minute                          |
//+------------------------------------------------------------------+
void OnNewBar(MqlRates &rates[])
{
   double o = rates[1].open;
   double h = rates[1].high;
   double l = rates[1].low;
   double c = rates[1].close;
   double barRange = h - l;
   
   // Update median range
   UpdateMedianRange(barRange);
   
   // Check if bar was a spike
   bool isSpike = (barRange > SpikeThreshold * g_medianRange);
   bool isCrashSpike = isSpike && (c < o);
   bool isBoomSpike = isSpike && (c > o);
   bool isMySpike = IsCrash() ? isCrashSpike : isBoomSpike;
   
   if(isMySpike && g_medianRange > 0)
   {
      g_lastSpikeBar = g_barCount;
      g_barsSinceSpike = 0;
      g_requiresNewSpike = false;  // New spike seen — allowed to enter
      g_spikes[g_spikeCount % g_spikeMax] = g_barCount;
      g_spikeCount++;
      Print("[SPIKE #", g_spikeCount, "] Bar ", g_barCount,
            " Range=", DoubleToString(barRange,5),
            " Median=", DoubleToString(g_medianRange,5),
            " Ratio=", DoubleToString(barRange/g_medianRange,1), "x");
   }
   else
   {
      g_barsSinceSpike++;
   }
   
   // Position management
   if(g_inPosition)
   {
      g_barsHeld++;
      
      // Check TP/SL/timeout
      if(CheckExitLevels(rates))
         return;  // Position closed
      
      // Timeout
      if(g_barsHeld >= MaxHoldBars)
      {
         double pnl = CalculateCurrentPnL(c);
         ClosePosition("timeout", pnl);
      }
   }
   else
   {
   // Check entry (only at exact bar after spike)
   if(!g_inPosition && g_barsSinceSpike == EntryBar)
   {
      // COLD hours filter — only trade during safe hours
      int currentHour = Hour();
      g_isColdHour = IsColdHour(currentHour);
      
      if(!g_isColdHour)
      {
         // NOT a COLD hour — skip entry
         g_blockedByCold++;
         Print("[COLD BLOCK] Hour=", currentHour, " (not safe) — skipping entry");
      }
      else
      {
         CheckEntry();
      }
   }
   }
   
   if(ShowInfo) UpdatePanel();
}

//+------------------------------------------------------------------+
//| Update median range (fast circular buffer)                         |
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
   if(g_barsSinceSpike != EntryBar) return;  // Enter at EXACTLY EntryBar bars
   if(g_lastSpikeBar < 0) return;
   if(g_medianRange <= 0) return;
   
   // Cluster filter
   int clusterCount = 0;
   for(int i = 0; i < g_spikeCount; i++)
   {
      int spikeBar = g_spikes[i];
      if(g_barCount - spikeBar <= ClusterWindow && spikeBar != g_lastSpikeBar)
         clusterCount++;
   }
   if(clusterCount >= ClusterMin - 1) return;
   
   // Trend filter
   if(UseTrendFilter && !CheckTrend()) return;
   
   // Must have seen a new spike since last trade closed
   if(g_requiresNewSpike) return;
   
   // Direction: Crash=LONG (buy dip), Boom=SHORT (fade spike)
   int dir = IsCrash() ? 1 : -1;
   
   // Calculate lot size
   double sl = SLMultiplier * g_medianRange;
   double lotSize = CalculateLotSize(sl);
   if(lotSize <= 0) {
      Print("[SKIP] Lot size too small. SL=", DoubleToString(sl,5),
            " Median=", DoubleToString(g_medianRange,5),
            " Equity=$", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2));
      return;
   }
   
   // Calculate TP/SL levels
   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
   double tp = TPMultiplier * g_medianRange;
   
   // Open position
   bool success = false;
   if(dir == 1)
      success = OpenBuy(lotSize, tp, sl);
   else
      success = OpenSell(lotSize, tp, sl);
   
   if(success)
   {
      g_inPosition = true;
      g_requiresNewSpike = true;  // Reset — need new spike before next entry
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
   
   if(g_positionDir == 1)  // Long
   {
      hitTP = (h - g_entryPrice) >= tp;
      hitSL = (g_entryPrice - l) >= sl;
   }
   else  // Short
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
//| Indicator handles (created once in OnInit)                         |
//+------------------------------------------------------------------+
int g_hEmaFast = INVALID_HANDLE;
int g_hEmaSlow = INVALID_HANDLE;

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
      return emaFast[0] > emaSlow[0];  // Uptrend = buy dip
   else
      return emaFast[0] < emaSlow[0];  // Downtrend = fade spike
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
//| Auto-detect filling mode for current broker                       |
//+------------------------------------------------------------------+
void DetectFillingMode()
{
   long fillMode = SymbolInfoInteger(Symbol(), SYMBOL_FILLING_MODE);
   
   if((fillMode & SYMBOL_FILLING_FOK) != 0)
      g_fillingMode = ORDER_FILLING_FOK;
   else if((fillMode & SYMBOL_FILLING_IOC) != 0)
      g_fillingMode = ORDER_FILLING_IOC;
   else
      g_fillingMode = ORDER_FILLING_RETURN;
   
   Print("[FILL] Auto-detected filling mode: ", EnumToString(g_fillingMode),
         " (FOK=", ((fillMode & SYMBOL_FILLING_FOK) != 0),
         " IOC=", ((fillMode & SYMBOL_FILLING_IOC) != 0),
         " RETURN=", ((fillMode & SYMBOL_FILLING_RETURN) != 0), ")");
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
   request.deviation = 500;
   request.magic = 202603;
   request.comment = "SC2_BUY";
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = g_fillingMode;
   request.sl = NormalizeDouble(ask - sl, digits);
   request.tp = NormalizeDouble(ask + tp, digits);
   
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
   request.deviation = 500;
   request.magic = 202603;
   request.comment = "SC2_SELL";
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = g_fillingMode;
   request.sl = NormalizeDouble(bid + sl, digits);
   request.tp = NormalizeDouble(bid - tp, digits);
   
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
//| Close position — FASTEST POSSIBLE                                  |
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
      request.deviation = 500;  // Wide deviation for safety exit — must fill during spikes
      request.magic = 202603;
      request.comment = "SC2_" + reason;
      request.type_time = ORDER_TIME_GTC;
      request.type_filling = g_fillingMode;
      
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
      
      // Update stats
      g_totalTrades++;
      g_totalPnL += pnl;
      if(pnl > 0) g_wins++; else g_losses++;
      
      double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;
      string emoji = (pnl > 0) ? "+" : "";
      Print("[", reason, "] PnL=$", DoubleToString(pnl, 4),
            " | ", g_totalTrades, "T WR=", DoubleToString(wr, 1), "%",
            " PnL=$", DoubleToString(g_totalPnL, 4),
            " Safety=", g_safetyExits);
      
      double exitPrice = (g_positionDir == 1) ? 
         SymbolInfoDouble(Symbol(), SYMBOL_BID) : 
         SymbolInfoDouble(Symbol(), SYMBOL_ASK);
      LogTrade("CLOSE", (g_positionDir == 1 ? "LONG" : "SHORT"),
               g_entryPrice, exitPrice, pnl, reason);
      
      // Track velocity exit stats
      if(reason == "velocity_exit")
      {
         // Calculate damage saved: estimate what the spike would have cost
         double estimatedSpikeDamage = g_medianRange * 3.0 * SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE) / SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
         double actualDamage = MathAbs(pnl);
         double damageSaved = estimatedSpikeDamage - actualDamage;
         if(damageSaved < 0) damageSaved = 0;
         g_velocityDamageSaved += damageSaved;
         
         string dirStr = (g_positionDir == 1) ? "LONG" : "SHORT";
         string status = (pnl >= 0) ? "WIN" : "LOSS";
         LogVelocityExit(dirStr, g_entryPrice, exitPrice,
                        g_consecutiveBuildup, pnl, damageSaved, status);
         
         Print("[VELOCITY EXIT] Buildup=", g_consecutiveBuildup, 
               " PnL=$", DoubleToString(pnl, 4),
               " Saved=$", DoubleToString(damageSaved, 4),
               " | Total saved=$", DoubleToString(g_velocityDamageSaved, 4));
      }
      
      // Reset
      g_inPosition = false;
      g_requiresNewSpike = true;  // Must see new spike before next entry
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
//| COLD Hours — Initialize from symbol-specific string               |
//+------------------------------------------------------------------+
void InitColdHours()
{
   string coldStr = "";
   string name = Symbol();
   StringToLower(name);
   
   if(StringFind(name, "boom500") >= 0 || StringFind(name, "boom_500") >= 0)
      coldStr = ColdHours_BOOM500;
   else if(StringFind(name, "crash500") >= 0 || StringFind(name, "crash_500") >= 0)
      coldStr = ColdHours_CRASH500;
   else if(StringFind(name, "boom1000") >= 0 || StringFind(name, "boom_1000") >= 0)
      coldStr = ColdHours_BOOM1000;
   else if(StringFind(name, "crash1000") >= 0 || StringFind(name, "crash_1000") >= 0)
      coldStr = ColdHours_CRASH1000;
   else
   {
      // Default: allow all hours for unknown symbols
      coldStr = "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23";
      Print("[COLD] Unknown symbol: ", Symbol(), " — allowing ALL hours");
   }
   
   // Parse comma-separated string into array
   string parts[];
   g_coldCount = StringSplit(coldStr, ',', parts);
   ArrayResize(g_coldHours, g_coldCount);
   
   for(int i = 0; i < g_coldCount; i++)
      g_coldHours[i] = (int)StringToInteger(parts[i]);
   
   Print("[COLD] Trading allowed during hours: ", coldStr);
}

//+------------------------------------------------------------------+
//| COLD Hours — Check if current hour is safe to trade                |
//+------------------------------------------------------------------+
bool IsColdHour(int hour)
{
   if(!UseColdHours) return true;  // Filter disabled = always OK
   
   for(int i = 0; i < g_coldCount; i++)
   {
      if(g_coldHours[i] == hour) return true;
   }
   return false;
}

//+------------------------------------------------------------------+
//| COLD Hours — Get string representation for logging                 |
//+------------------------------------------------------------------+
string GetColdHoursString()
{
   string result = "";
   for(int i = 0; i < g_coldCount; i++)
   {
      if(i > 0) result += ",";
      result += IntegerToString(g_coldHours[i]);
   }
   return result;
}

//+------------------------------------------------------------------+
//| FILE I/O                                                           |
//+------------------------------------------------------------------+
void InitFiles()
{
   // NOTE: Do NOT use FileCreate(folder) — it creates a FILE, not a directory.
   // FileOpen with FILE_WRITE|FILE_CSV will create intermediate dirs automatically.
   string folder = "SpikeCatcher";
   g_logFile = folder + "\\trades_" + Symbol() + ".csv";
   g_configFile = folder + "\\config_" + Symbol() + ".json";
   g_velocityLogFile = folder + "\\velocity_exits_" + Symbol() + ".csv";
   
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
   
   if(!FileIsExist(g_velocityLogFile, FILE_COMMON))
   {
      int h = FileOpen(g_velocityLogFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
      if(h != INVALID_HANDLE)
      {
         FileWrite(h, "time","symbol","direction","entry","exit","buildup",
                   "pnl","damage_saved","status");
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

void LogVelocityExit(string direction, double entry, double exit,
                     int buildup, double pnl, double damageSaved, string status)
{
   int h = FileOpen(g_velocityLogFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   
   string time = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
   FileWrite(h, time, Symbol(), direction,
             DoubleToString(entry, 5), DoubleToString(exit, 5),
             IntegerToString(buildup),
             DoubleToString(pnl, 4), DoubleToString(damageSaved, 4),
             status);
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
   
   CreateLabel(p+"t1", x, y, "SPIKE CATCHER v2 — " + Symbol(), clrWhite, 10); y+=20;
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
   CreateLabel(p+"t6", x, y, "Safety: "+IntegerToString(g_safetyExits)+" Velocity: "+IntegerToString(g_velocityExits)+" Spikes: "+IntegerToString(g_spikesDetected), clrYellow, 9); y+=18;
   
   // COLD hours status
   int curHour = Hour();
   bool curCold = IsColdHour(curHour);
   string coldStatus = curCold ? "COLD (safe)" : "HOT (blocked)";
   color coldClr = curCold ? clrLime : clrRed;
   CreateLabel(p+"t8b", x, y, "Hour: "+IntegerToString(curHour)+":00 = "+coldStatus+" | Blocked: "+IntegerToString(g_blockedByCold), coldClr, 9); y+=18;
   
   // Buildup bar: visual indicator of buildup progress
   string buildupBar = "";
   int barLen = 20;  // Max bar length
   int filledBars = (int)MathMin(g_consecutiveBuildup * barLen / VelocityThreshold, barLen);
   for(int i = 0; i < barLen; i++)
   {
      if(i < filledBars)
         buildupBar += "|";
      else
         buildupBar += ".";
   }
   
   color buildupClr = clrAqua;
   if(g_consecutiveBuildup >= VelocityThreshold)
      buildupClr = clrRed;  // TRIGGERED!
   else if(g_consecutiveBuildup >= VelocityThreshold * 0.7)
      buildupClr = clrOrange;  // Warning zone
   
   CreateLabel(p+"t7", x, y, "Buildup: ["+buildupBar+"] "+IntegerToString(g_consecutiveBuildup)+"/"+IntegerToString(VelocityThreshold), buildupClr, 9); y+=18;
   
   if(g_velocityExits > 0)
   {
      double avgSaved = g_velocityDamageSaved / g_velocityExits;
      CreateLabel(p+"t8", x, y, "Vel Saved: $"+DoubleToString(g_velocityDamageSaved,2)+" (avg $"+DoubleToString(avgSaved,2)+"/exit)", clrLime, 9);
   }
   else
   {
      CreateLabel(p+"t8", x, y, "Vel Saved: $0.00 (no exits yet)", clrGray, 9);
   }
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

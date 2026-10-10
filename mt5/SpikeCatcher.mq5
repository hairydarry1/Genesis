//+------------------------------------------------------------------+
//|                                              SpikeCatcher.mq5    |
//|                              BOOM/CRASH Counter-Move Strategy     |
//|                                                                    |
//| Strategy:                                                          |
//|   1. Detect spike (move > 3x median range)                        |
//|   2. Wait for spike to end (3 ticks over ~3 seconds)             |
//|   3. Enter counter-position (LONG crash dip, SHORT boom fade)     |
//|   4. TP = 2x median range, SL = 1x median range                  |
//|   5. SAFETY: If new spike detected while in trade → CLOSE FAST    |
//|                                                                    |
//| Works on: Crash 300, Crash 500, Boom 300, Boom 500               |
//+------------------------------------------------------------------+
#property copyright "Genesis Trading System"
#property version   "1.00"
#property strict

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                   |
//+------------------------------------------------------------------+
input group "=== Risk Management ==="
input double RiskPercent    = 2.0;    // Risk per trade (% of equity)
input double MaxLots        = 5.0;    // Maximum lot size
input int    MaxPositions   = 1;      // Max simultaneous positions

input group "=== Spike Detection ==="
input int    SpikeLookback  = 100;    // Ticks to calculate median range
input double SpikeThreshold = 3.0;    // Range > Nx median = spike
input int    SpikeCooldown  = 2;      // Bars to wait after spike before entry
input double MinSpikeSize   = 0.05;   // Minimum spike size (price) to trigger

input group "=== Trade Management ==="
input double TPMultiplier   = 2.0;    // TP = Nx median range
input double SLMultiplier   = 1.0;    // SL = Nx median range
input int    MaxHoldBars    = 9;      // Max bars to hold (timeout exit)
input int    ClusterWindow  = 10;     // Bars to check for spike clusters
input int    ClusterMin     = 3;      // Min spikes in window = cluster (skip)

input group "=== Trend Filter ==="
input bool   UseTrendFilter = true;   // Use M5 EMA trend filter
input int    EMA_Fast       = 10;     // Fast EMA period
input int    EMA_Slow       = 30;     // Slow EMA period

input group "=== Safety Exit ==="
input bool   UseSafetyExit  = true;   // Close on new spike detection
input double SafetyMultiplier = 3.0;  // Detection threshold for safety exit

input group "=== Display ==="
input bool   ShowInfo       = true;   // Show info panel on chart
input color  PanelColor     = clrBlack;
input color  TextColor      = clrWhite;
input color  WinColor       = clrLime;
input color  LossColor      = clrRed;

//+------------------------------------------------------------------+
//| GLOBAL VARIABLES                                                   |
//+------------------------------------------------------------------+
// Spike detection
double g_tickBuffer[];          // Circular buffer of recent tick prices
int    g_tickIndex = 0;         // Current position in buffer
double g_medianRange = 0;       // Median range of recent bars
int    g_spikes[];              // Bar indices of detected spikes
int    g_spikeCount = 0;        // Number of spikes tracked

// Position state
bool   g_inPosition = false;    // Currently in a trade
int    g_positionDir = 0;       // 1=long, -1=short
double g_entryPrice = 0;        // Entry price
int    g_entryBar = 0;          // Bar index at entry
int    g_barsHeld = 0;          // Bars since entry

// Spike state  
bool   g_inSpike = false;       // Currently in a spike
int    g_spikeTicks = 0;        // Ticks since spike started
double g_spikeStartPrice = 0;   // Price at spike start
double g_spikeSize = 0;         // Current spike size
int    g_lastSpikeBar = -999;   // Bar index of last spike
int    g_barsSinceSpike = 999;  // Bars since last spike

// Tick tracking
double g_lastTickPrice = 0;     // Previous tick price
double g_tickMove = 0;          // Current tick move
double g_maxTickMove = 0;       // Max move in current spike

// Performance tracking
int    g_totalTrades = 0;
int    g_wins = 0;
int    g_losses = 0;
double g_totalPnL = 0;
double g_peakEquity = 0;
int    g_safetyExits = 0;

// Bar tracking
int    g_lastBarTime = 0;       // To detect new M1 bar
int    g_barCount = 0;          // Total bars processed

// Range tracking
double g_ranges[];              // Recent bar ranges
int    g_rangeCount = 0;

// Chart objects
string g_panelName = "SpikeCatcher";

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   // Initialize tick buffer
   ArrayResize(g_tickBuffer, SpikeLookback);
   ArrayInitialize(g_tickBuffer, 0);
   
   // Initialize range buffer
   ArrayResize(g_ranges, SpikeLookback);
   ArrayInitialize(g_ranges, 0);
   
   // Initialize spike array
   ArrayResize(g_spikes, 100);
   
   // Get current equity
   g_peakEquity = AccountInfoDouble(ACCOUNT_EQUITY);
   
   // Initialize file I/O
   InitFiles();
   
   Print("SpikeCatcher initialized on ", Symbol());
   Print("Risk: ", RiskPercent, "% | TP: ", TPMultiplier, "x | SL: ", SLMultiplier, "x");
   Print("Spike threshold: ", SpikeThreshold, "x | Safety exit: ", UseSafetyExit ? "ON" : "OFF");
   
   // Set timer for periodic updates
   EventSetMillisecondTimer(100);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   CleanupPanel();
   Print("SpikeCatcher stopped. Trades: ", g_totalTrades, 
         " WR: ", g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0, "%",
         " PnL: $", DoubleToString(g_totalPnL, 2));
}

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
{
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   if(price <= 0) return;
   
   // Track tick move
   if(g_lastTickPrice > 0)
   {
      g_tickMove = MathAbs(price - g_lastTickPrice);
      g_lastTickPrice = price;
   }
   else
   {
      g_lastTickPrice = price;
      return;
   }
   
   // Update tick buffer
   g_tickBuffer[g_tickIndex % SpikeLookback] = price;
   g_tickIndex++;
   
   // Check for new M1 bar
   MqlRates rates[];
   if(CopyRates(Symbol(), PERIOD_M1, 0, 3, rates) < 3) return;
   
   int currentBarTime = (int)rates[0].time;
   if(currentBarTime != g_lastBarTime)
   {
      // New bar — process bar-level logic
      OnNewBar(rates);
      g_lastBarTime = currentBarTime;
      g_barCount++;
   }
   
   // TICK-LEVEL: Detect spike start (immediate)
   DetectSpikeStart(price);
   
   // TICK-LEVEL: Safety exit check
   if(UseSafetyExit && g_inPosition && g_inSpike)
   {
      SafetyExit(price);
   }
}

//+------------------------------------------------------------------+
//| New M1 bar processing                                              |
//+------------------------------------------------------------------+
void OnNewBar(MqlRates &rates[])
{
   double o = rates[1].open;
   double h = rates[1].high;
   double l = rates[1].low;
   double c = rates[1].close;
   double barRange = h - l;
   
   // Update range buffer
   g_rangeCount++;
   if(g_rangeCount > SpikeLookback) g_rangeCount = SpikeLookback;
   g_ranges[(g_rangeCount - 1) % SpikeLookback] = barRange;
   
   // Calculate median range
   double tempRanges[];
   ArrayResize(tempRanges, g_rangeCount);
   for(int i = 0; i < g_rangeCount; i++)
      tempRanges[i] = g_ranges[i];
   ArraySort(tempRanges);
   g_medianRange = tempRanges[g_rangeCount / 2];
   
   // Check if bar was a spike
   bool isSpike = (barRange > SpikeThreshold * g_medianRange);
   bool isCrashSpike = isSpike && (c < o);  // Crash: spike DOWN
   bool isBoomSpike = isSpike && (c > o);   // Boom: spike UP
   
   bool isSymbolSpike = false;
   if(IsCrash())
      isSymbolSpike = isCrashSpike;
   else
      isSymbolSpike = isBoomSpike;
   
   if(isSymbolSpike)
   {
      g_lastSpikeBar = g_barCount;
      g_barsSinceSpike = 0;
      g_spikes[g_spikeCount % 100] = g_barCount;
      g_spikeCount++;
      
      Print("[SPIKE] Bar ", g_barCount, " Size=", DoubleToString(barRange, 4),
            " (", DoubleToString(barRange/g_medianRange, 1), "x median)");
   }
   else
   {
      g_barsSinceSpike++;
   }
   
   // Reset spike state on new bar
   g_inSpike = false;
   g_spikeTicks = 0;
   g_maxTickMove = 0;
   
   // Read config from Python (check every 10 bars)
   if(g_barCount % 10 == 0)
      ReadConfig();
   
   // Position management
   if(g_inPosition)
   {
      g_barsHeld++;
      CheckExit(rates);
   }
   else
   {
      // Entry logic: after spike cooldown
      CheckEntry(rates);
   }
   
   // Update chart
   if(ShowInfo) UpdatePanel();
}

//+------------------------------------------------------------------+
//| Detect spike start at tick level                                   |
//+------------------------------------------------------------------+
void DetectSpikeStart(double price)
{
   if(g_tickIndex < 2) return;
   
   // Check if current tick is a big move
   if(g_tickMove > SpikeThreshold * g_medianRange * 0.1 && g_medianRange > 0)
   {
      if(!g_inSpike)
      {
         g_inSpike = true;
         g_spikeStartPrice = price;
         g_spikeTicks = 0;
         g_maxTickMove = g_tickMove;
         
         Print("[SPIKE START] Tick ", g_tickIndex, " Move=", 
               DoubleToString(g_tickMove, 5), " Price=", DoubleToString(price, 3));
      }
      g_spikeTicks++;
      g_maxTickMove = MathMax(g_maxTickMove, g_tickMove);
   }
}

//+------------------------------------------------------------------+
//| Safety exit — close immediately on spike detection                 |
//+------------------------------------------------------------------+
void SafetyExit(double price)
{
   // Calculate damage so far
   double damage = 0;
   if(g_positionDir == 1)  // Long position
      damage = g_entryPrice - price;  // Negative = bad
   else  // Short position
      damage = price - g_entryPrice;  // Negative = bad
   
   // If spike is moving against us, close immediately
   bool spikeAgainstUs = false;
   if(g_positionDir == 1 && price < g_entryPrice)
      spikeAgainstUs = true;
   if(g_positionDir == -1 && price > g_entryPrice)
      spikeAgainstUs = true;
   
   if(spikeAgainstUs && g_spikeTicks >= 1)
   {
      Print("[SAFETY EXIT] Spike against us! Closing position...");
      ClosePosition("safety_spike");
      g_safetyExits++;
   }
}

//+------------------------------------------------------------------+
//| Check for entry after spike                                        |
//+------------------------------------------------------------------+
void CheckEntry(MqlRates &rates[])
{
   if(g_inPosition) return;
   if(g_barsSinceSpike != SpikeCooldown) return;
   if(g_lastSpikeBar < 0) return;
   
   // Cluster filter
   int clusterCount = 0;
   for(int i = 0; i < g_spikeCount; i++)
   {
      int spikeBar = g_spikes[i];
      if(g_barCount - spikeBar <= ClusterWindow && spikeBar != g_lastSpikeBar)
         clusterCount++;
   }
   if(clusterCount >= ClusterMin - 1) return;  // Cluster — skip
   
   // Trend filter
   if(UseTrendFilter && !CheckTrend()) return;
   
   // Enter counter-position
   double ask = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double bid = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   
   double tp = TPMultiplier * g_medianRange;
   double sl = SLMultiplier * g_medianRange;
   
   // Crash: LONG after spike (buy the dip)
   // Boom: SHORT after spike (fade the spike)
   int dir = IsCrash() ? 1 : -1;
   
   // Calculate lot size
   double lotSize = CalculateLotSize(sl);
   if(lotSize <= 0) return;
   
   // Send order
   bool success = false;
   if(dir == 1)
      success = OpenBuy(lotSize, tp, sl);
   else
      success = OpenSell(lotSize, tp, sl);
   
   if(success)
   {
      g_inPosition = true;
      g_positionDir = dir;
      g_entryPrice = dir == 1 ? ask : bid;
      g_entryBar = g_barCount;
      g_barsHeld = 0;
      
      Print("[ENTRY] ", (dir == 1 ? "LONG" : "SHORT"), 
            " @ ", DoubleToString(g_entryPrice, 3),
            " TP=", DoubleToString(tp, 3),
            " SL=", DoubleToString(sl, 3),
            " Lots=", DoubleToString(lotSize, 2));
      
      LogEntry();
   }
}

//+------------------------------------------------------------------+
//| Check exit conditions                                              |
//+------------------------------------------------------------------+
void CheckExit(MqlRates &rates[])
{
   if(!g_inPosition) return;
   
   double h = rates[0].high;
   double l = rates[0].low;
   double c = rates[0].close;
   double tp = TPMultiplier * g_medianRange;
   double sl = SLMultiplier * g_medianRange;
   
   bool hitTP = false;
   bool hitSL = false;
   bool timeout = false;
   
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
   
   timeout = (g_barsHeld >= MaxHoldBars);
   
   if(hitTP)
   {
      ClosePosition("tp");
   }
   else if(hitSL)
   {
      ClosePosition("sl");
   }
   else if(timeout)
   {
      ClosePosition("timeout");
   }
}

//+------------------------------------------------------------------+
//| Check M5 trend filter                                              |
//+------------------------------------------------------------------+
bool CheckTrend()
{
   double emaFast[], emaSlow[];
   ArrayResize(emaFast, 1);
   ArrayResize(emaSlow, 1);
   
   int handleFast = iMA(Symbol(), PERIOD_M5, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   int handleSlow = iMA(Symbol(), PERIOD_M5, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   
   if(handleFast == INVALID_HANDLE || handleSlow == INVALID_HANDLE) return true;
   
   if(CopyBuffer(handleFast, 0, 0, 1, emaFast) < 1) return true;
   if(CopyBuffer(handleSlow, 0, 0, 1, emaSlow) < 1) return true;
   
   IndicatorRelease(handleFast);
   IndicatorRelease(handleSlow);
   
   // Crash: buy dip in uptrend (fast > slow)
   // Boom: fade spike in downtrend (fast < slow)
   if(IsCrash())
      return emaFast[0] > emaSlow[0];  // Uptrend = good for LONG
   else
      return emaFast[0] < emaSlow[0];  // Downtrend = good for SHORT
}

//+------------------------------------------------------------------+
//| Calculate lot size based on risk                                    |
//+------------------------------------------------------------------+
double CalculateLotSize(double slDistance)
{
   if(slDistance <= 0) return 0;
   
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double riskAmount = equity * RiskPercent / 100.0;
   
   // Tick value calculation
   double tickValue = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
   double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   
   if(tickValue <= 0 || tickSize <= 0) return SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
   
   // Value per point
   double valuePerPoint = tickValue * (point / tickSize);
   
   // Lot size = risk / (SL distance * value per point)
   double lots = riskAmount / (slDistance * valuePerPoint);
   
   // Apply limits
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
   
   request.action = TRADE_ACTION_DEAL;
   request.symbol = Symbol();
   request.volume = lots;
   request.type = ORDER_TYPE_BUY;
   request.price = SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   request.deviation = 100;
   request.magic = 202603;
   request.comment = "SC_BUY";
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = ORDER_FILLING_IOC;
   
   // Set TP/SL
   request.sl = SymbolInfoDouble(Symbol(), SYMBOL_ASK) - sl * SymbolInfoDouble(Symbol(), SYMBOL_POINT) / SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   request.tp = SymbolInfoDouble(Symbol(), SYMBOL_ASK) + tp * SymbolInfoDouble(Symbol(), SYMBOL_POINT) / SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   
   // Simpler: use price levels
   double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
   
   request.price = NormalizeDouble(SymbolInfoDouble(Symbol(), SYMBOL_ASK), digits);
   request.sl = NormalizeDouble(request.price - sl, digits);
   request.tp = NormalizeDouble(request.price + tp, digits);
   
   bool sent = OrderSend(request, result);
   
   if(!sent || result.retcode != TRADE_RETCODE_DONE)
   {
      Print("[BUY FAIL] Error: ", result.retcode, " Comment: ", result.comment);
      return false;
   }
   
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
   request.deviation = 100;
   request.magic = 202603;
   request.comment = "SC_SELL";
   request.type_time = ORDER_TIME_GTC;
   request.type_filling = ORDER_FILLING_IOC;
   request.sl = NormalizeDouble(bid + sl, digits);
   request.tp = NormalizeDouble(bid - tp, digits);
   
   bool sent = OrderSend(request, result);
   
   if(!sent || result.retcode != TRADE_RETCODE_DONE)
   {
      Print("[SELL FAIL] Error: ", result.retcode, " Comment: ", result.comment);
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Close position                                                     |
//+------------------------------------------------------------------+
bool ClosePosition(string reason)
{
   if(!g_inPosition) return false;
   
   // Find our position
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
      request.deviation = 100;
      request.magic = 202603;
      request.comment = "SC_CLOSE_" + reason;
      request.type_time = ORDER_TIME_GTC;
      request.type_filling = ORDER_FILLING_IOC;
      
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
         Print("[CLOSE FAIL] Error: ", result.retcode, " Comment: ", result.comment);
         return false;
      }
      
      // Calculate PnL
      double pnl = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      
      g_totalTrades++;
      g_totalPnL += pnl;
      if(pnl > 0) g_wins++; else g_losses++;
      
      Print("[CLOSE] ", reason, " PnL=$", DoubleToString(pnl, 4),
            " | Total: ", g_totalTrades, " trades, WR=", 
            DoubleToString(g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0, 1), "%",
            " PnL=$", DoubleToString(g_totalPnL, 4));
      
      LogClose(reason, pnl);
      
      // Reset state
      g_inPosition = false;
      g_positionDir = 0;
      g_entryPrice = 0;
      g_barsHeld = 0;
      
      return true;
   }
   
   return false;
}

//+------------------------------------------------------------------+
//| Check if symbol is crash                                           |
//+------------------------------------------------------------------+
bool IsCrash()
{
   string name = Symbol();
   StringToLower(name);
   return StringFind(name, "crash") >= 0;
}

//+------------------------------------------------------------------+
//| Timer function — periodic chart updates                            |
//+------------------------------------------------------------------+
void OnTimer()
{
   if(ShowInfo) UpdatePanel();
}

//+------------------------------------------------------------------+
//| Update chart info panel                                            |
//+------------------------------------------------------------------+
void UpdatePanel()
{
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   
   int x = 10, y = 30;
   string prefix = g_panelName + "_";
   
   // Title
   CreateLabel(prefix + "title", x, y, "SPIKE CATCHER — " + Symbol(), TextColor, 10);
   y += 20;
   
   // Price and equity
   double price = SymbolInfoDouble(Symbol(), SYMBOL_BID);
   CreateLabel(prefix + "price", x, y, "Price: " + DoubleToString(price, 3) + 
               "  Equity: $" + DoubleToString(equity, 2), TextColor, 9);
   y += 18;
   
   // Range info
   CreateLabel(prefix + "range", x, y, "Median Range: " + DoubleToString(g_medianRange, 4) +
               "  Bars: " + IntegerToString(g_barCount), TextColor, 9);
   y += 18;
   
   // Position status
   string posStr = "FLAT";
   color posColor = TextColor;
   if(g_inPosition)
   {
      posStr = (g_positionDir == 1 ? "LONG" : "SHORT") + 
               " @ " + DoubleToString(g_entryPrice, 3) +
               " (" + IntegerToString(g_barsHeld) + " bars)";
      posColor = g_positionDir == 1 ? WinColor : LossColor;
   }
   CreateLabel(prefix + "pos", x, y, "Position: " + posStr, posColor, 9);
   y += 18;
   
   // Spike status
   string spikeStr = "No spike";
   color spikeColor = TextColor;
   if(g_inSpike)
   {
      spikeStr = "SPIKE IN PROGRESS (" + IntegerToString(g_spikeTicks) + " ticks)";
      spikeColor = LossColor;
   }
   CreateLabel(prefix + "spike", x, y, "Spike: " + spikeStr, spikeColor, 9);
   y += 18;
   
   // Performance
   double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;
   CreateLabel(prefix + "perf", x, y, "Trades: " + IntegerToString(g_totalTrades) +
               "  WR: " + DoubleToString(wr, 1) + "%" +
               "  PnL: $" + DoubleToString(g_totalPnL, 4) +
               "  Safety: " + IntegerToString(g_safetyExits), TextColor, 9);
   y += 18;
   
   // Trend
   if(UseTrendFilter)
   {
      bool trend = CheckTrend();
      CreateLabel(prefix + "trend", x, y, "Trend: " + (trend ? "FAVORABLE" : "AGAINST"), 
                  trend ? WinColor : LossColor, 9);
      y += 18;
   }
}

//+------------------------------------------------------------------+
//| Create chart label                                                 |
//+------------------------------------------------------------------+
void CreateLabel(string name, int x, int y, string text, color clr, int fontSize)
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
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
   ObjectSetString(0, name, OBJPROP_FONT, "Consolas");
}

//+------------------------------------------------------------------+
//| Cleanup chart objects                                               |
//+------------------------------------------------------------------+
void CleanupPanel()
{
   ObjectsDeleteAll(0, g_panelName);
}

//+------------------------------------------------------------------+
//| FILE I/O — Trade logging & config sync with Python                |
//+------------------------------------------------------------------+
string g_logFile;
string g_configFile;

void InitFiles()
{
   string folder = "SpikeCatcher";
   g_logFile = folder + "\\trades_" + Symbol() + ".csv";
   g_configFile = folder + "\\config_" + Symbol() + ".json";
   
   // Create folder
   FileCreate(folder, FILE_COMMON);
   
   // Write CSV header if file is new
   if(!FileIsExist(g_logFile, FILE_COMMON))
   {
      int handle = FileOpen(g_logFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
      if(handle != INVALID_HANDLE)
      {
         FileWrite(handle, "time","symbol","action","direction","entry","exit",
                   "pnl","pnl_pct","reason","bars_held","equity",
                   "tp_mult","sl_mult","spike_threshold","cluster_count");
         FileClose(handle);
      }
   }
   
   Print("Files initialized: ", g_logFile);
}

void LogTrade(string action, string direction, double entry, double exit,
              double pnl, double pnlPct, string reason, int barsHeld)
{
   int handle = FileOpen(g_logFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
   if(handle == INVALID_HANDLE) return;
   
   // Seek to end
   FileSeek(handle, 0, SEEK_END);
   
   string time = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
   
   FileWrite(handle, time, Symbol(), action, direction,
             DoubleToString(entry, 5), DoubleToString(exit, 5),
             DoubleToString(pnl, 4), DoubleToString(pnlPct, 4),
             reason, barsHeld, DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2),
             DoubleToString(TPMultiplier, 2), DoubleToString(SLMultiplier, 2),
             DoubleToString(SpikeThreshold, 2), g_spikeCount);
   FileClose(handle);
}

void LogEntry()
{
   string dir = (g_positionDir == 1) ? "LONG" : "SHORT";
   LogTrade("OPEN", dir, g_entryPrice, 0, 0, 0, "entry", 0);
}

void LogClose(string reason, double pnl)
{
   string dir = (g_positionDir == 1) ? "LONG" : "SHORT";
   double pnlPct = (g_entryPrice > 0) ? pnl / AccountInfoDouble(ACCOUNT_EQUITY) * 100 : 0;
   LogTrade("CLOSE", dir, g_entryPrice, 0, pnl, pnlPct, reason, g_barsHeld);
}

//+------------------------------------------------------------------+
//| Read config from Python (parameter updates)                       |
//+------------------------------------------------------------------+
void ReadConfig()
{
   if(!FileIsExist(g_configFile, FILE_COMMON)) return;
   
   int handle = FileOpen(g_configFile, FILE_COMMON|FILE_READ|FILE_TXT);
   if(handle == INVALID_HANDLE) return;
   
   string content = "";
   while(!FileIsEnding(handle))
      content += FileReadString(handle);
   FileClose(handle);
   
   // Simple JSON-like parsing
   // Format: {"tp_mult": 2.5, "sl_mult": 1.2, "spike_threshold": 3.5, ...}
   // This is a simplified parser — for full JSON use a library
   
   double newTP = ParseJsonDouble(content, "tp_mult", TPMultiplier);
   double newSL = ParseJsonDouble(content, "sl_mult", SLMultiplier);
   double newSpike = ParseJsonDouble(content, "spike_threshold", SpikeThreshold);
   double newRisk = ParseJsonDouble(content, "risk_percent", RiskPercent);
   int newCooldown = (int)ParseJsonDouble(content, "cooldown", SpikeCooldown);
   int newCluster = (int)ParseJsonDouble(content, "cluster_min", ClusterMin);
   
   bool changed = false;
   if(newTP != TPMultiplier) { TPMultiplier = newTP; changed = true; }
   if(newSL != SLMultiplier) { SLMultiplier = newSL; changed = true; }
   if(newSpike != SpikeThreshold) { SpikeThreshold = newSpike; changed = true; }
   if(newRisk != RiskPercent) { RiskPercent = newRisk; changed = true; }
   if(newCooldown != SpikeCooldown) { SpikeCooldown = newCooldown; changed = true; }
   if(newCluster != ClusterMin) { ClusterMin = newCluster; changed = true; }
   
   if(changed)
   {
      Print("[CONFIG UPDATED] TP=" + DoubleToString(TPMultiplier, 2) +
            " SL=" + DoubleToString(SLMultiplier, 2) +
            " Spike=" + DoubleToString(SpikeThreshold, 2) +
            " Risk=" + DoubleToString(RiskPercent, 2) + "%");
      
      // Delete config file after reading (one-time update)
      FileDelete(g_configFile, FILE_COMMON);
   }
}

double ParseJsonDouble(string json, string key, double defaultVal)
{
   string searchKey = "\"" + key + "\":";
   int pos = StringFind(json, searchKey);
   if(pos < 0) return defaultVal;
   
   string after = StringSubstr(json, pos + StringLen(searchKey));
   
   // Skip spaces
   int start = 0;
   while(start < StringLen(after) && StringGetCharacter(after, start) == ' ')
      start++;
   
   // Read number
   string num = "";
   for(int i = start; i < StringLen(after); i++)
   {
      ushort ch = StringGetCharacter(after, i);
      if((ch >= '0' && ch <= '9') || ch == '.' || ch == '-')
         num += ShortToString(ch);
      else
         break;
   }
   
   if(StringLen(num) == 0) return defaultVal;
   return StringToDouble(num);
}
//+------------------------------------------------------------------+

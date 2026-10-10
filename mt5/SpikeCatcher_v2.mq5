//+------------------------------------------------------------------+
//|                                         SpikeCatcher_v2.mq5      |
//|                              Optimized Counter-Move Strategy      |
//|                                                                    |
//| v2.16 - PHASE 1 FADE ALIGNMENT (docs/EA_PHASE1_ALIGNMENT_AUDIT.md):   |
//|   - ColdHoursList input (scattered hour list, UTC via TimeGMT): the    |
//|     validated edge trades [1,5,12,14,15,19], inexpressible as a range. |
//|   - Fade exits: TP = TPPercent % of entry checked on M1 CLOSE, no SL   |
//|     by default (SLMultiplier=0), hold 8 bars, tick safety exit OFF.    |
//|     Bar-close spike-against exit is now CORE (decoupled from the tick  |
//|     exit). Optional CatastropheSLMult = account-protection stop far    |
//|     outside the spike tail (fill-through is acceptable there; it is    |
//|     margin insurance, NOT the strategy's risk model — see              |
//|     docs/TAIL_RISK_STOP_LOSSES.md: size to the tail, never stop into). |
//|   - Sizing denominator fixed at 1.5x median (parity with validated     |
//|     USD-equivalents) regardless of stop configuration.                 |
//|   - Entry gates default OFF (cluster gate now has a flag). E-ladder    |
//|     evidence: mixed geometries all negative; cooldown-10 costs -$324/wk|
//|     on the fade.                                                       |
//|                                                                        |
//| v2.07 - Loss cooldown: after a losing close, skip the next N     |
//|         spikes before entering again. Losses cluster on Boom/Crash|
//|         (double spikes within 1-5 bars), so the spikes right after|
//|         a loss are the dangerous ones. Backtest-validated: cuts   |
//|         max consecutive losses roughly in half on all 4 symbols   |
//|         while keeping per-trade expectancy flat or better.        |
//|                                                                    |
//| v2.14 - Regime gate + bar cooldown (phase 1 of the mode rollout):
//|   - M15 break-of-structure gate: only fade discharges when the M15
//|     regime matches the drift direction (bull for crash, bear for boom).
//|     Sim: lifts every symbol (crash300 +0.27->+0.58/trade, boom1000
//|     +0.31->+1.05), OOS-consistent both halves on all 5 symbols.
//|   - Loss cooldown in BARS (default 10): after a loss, skip entries
//|     for N bars. Sim: bar-units ~2x EV vs spike-units — losses cluster
//|     in bars, not spikes (3 spikes ~40-100 bars overshot the plateau).
//|     Supersedes the spike-count cooldown (LossCooldown default now 0).
//|     Evidence: docs/SCHEDULE_OPT_VERDICT.md, docs/ICT_PULLBACK_VERDICT.md
//| v2.11 - Multi-position + risk/instance fixes:
//|   - ClosePosition closes ALL tickets (partial second halves are
//|     separate positions in hedging mode) & accounts REAL broker PnL
//|   - Lot size risks on the 1.5x attach distance stops actually get
//|   - Same-symbol instance lock (global-variable heartbeat) prevents
//|     double entries from a second EA on the same chart symbol
//|   - Safety tick threshold calibrated per symbol (300/500 tighter)
//|   - 60-bar median warm-up before entries are allowed
//| v2.15 - Trailing max-ratio gate (solo-core filter):
//|   - Skip entry if the largest adverse spike in the last 20 bars
//|     (excluding current) had ratio >= 8.0x median.
//|   - Verified OOS on corrected engine: crash300 diff +0.181
//|     (H1=+0.233, H2=+0.130, n=2220, 32% kept).
//|   - Auto-on for crash300 only. See docs/FINAL_VERIFIED_PLAYBOOK.md.
//+------------------------------------------------------------------+
#property copyright "Genesis Trading System v2"
#property version   "2.16"
#property strict

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                   |
//+------------------------------------------------------------------+
input group "=== Trading System ==="
input int    TradingSystem  = 1;      // 1=Max Profit, 2=Max Safety

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

input group "=== TP/SL (v2.16: Phase 1 fade semantics) ==="
input double TPPercent      = 0.4;    // TP = % of entry, checked on M1 CLOSE (fade)
input double SLMultiplier   = 0.0;    // SL = Nx median range; 0 = NO SL (fade semantics)
input double CatastropheSLMult = 80.0; // Account-protection SL at Nx median (0=off; > p99 spike tail)
input double TPGradFactor = 1.0;      // Legacy intrabar median-TP factor (0 = off; EA-scalp only)
input double TPMultiplier   = 2.0;    // LEGACY scalp TP = Nx median (only read when TPGradFactor > 0)

input group "=== Filters (v2.16: default OFF = validated fade baseline) ==="
input bool   UseClusterGate = false; // Skip when >= ClusterMin spikes in window (untested on fade)
input int    ClusterWindow  = 10;     // Bars to check for clusters
input int    ClusterMin     = 3;      // Min spikes in window = skip
input bool   UseTrendFilter = false;  // Use M5 EMA trend filter
input int    EMA_Fast       = 10;     // Fast EMA period
input int    EMA_Slow       = 30;     // Slow EMA period

input group "=== Safety Filters (v2.16) ==="
input bool   SkipDoubleSpikes = false; // Skip if previous spike was 1-3 bars ago (untested on fade)
input int    DoubleSpikeWindow = 3;   // Bars to check for double spikes
input bool   UseColdHours    = true;  // Only trade during COLD hours
input string ColdHoursList   = "1,5,12,14,15,19"; // Comma-separated UTC hours (the validated list)
input int    ColdHoursStart  = 0;     // LEGACY range start (only if ColdHoursList empty)
input int    ColdHoursEnd    = 6;     // LEGACY range end (only if ColdHoursList empty)

input group "=== Safety Exit (v2.16: OFF by default — bar-close spike_against is core) ==="
input bool   UseSafetyExit  = false;  // Tick-level spike exit (EA-scalp only: -$931/wk on the fade)
input double SafetyMoveMult = 2.5;    // Tick move > Nx median = spike (lower = faster)

input group "=== Partial Entry (v2.16: OFF — untested on fade; exposure-path change) ==="
input bool   UsePartialEntry = false; // Enter 50% now, add 50% after 1 bar if winning

input group "=== Loss Protection ==="
input int    LossCooldown    = 0;     // Spikes to skip after a loss (superseded by LossCooldownBars; 0=off)

input group "=== Big-Cluster Gate (quality filter) ==="
input bool   UseBigClusterGate = true; // Only enter after BIG spikes in groups
input double BigClusterMult   = 8.0;  // Spike must be >= Nx median range
input int    BigClusterWindow = 3;    // ...with another >=Nx spike within N bars
                              // Verified: 300/500 only — auto-off on 1000 Index
                              // win=3 beats win=5 OOS on all 3 gated symbols

input group "=== Trailing Max-Ratio Gate (solo-core filter, v2.15) ==="
input bool   UseTrailingMaxRatioGate = false; // Manual override (crash300 auto-on; set false to disable)
input double TrailingMaxRatioThreshold = 8.0;  // N bars (excl. current) >= threshold.
input int    TrailingMaxRatioWindow = 20;      // Verified OOS: crash300 th=8.0 (diff +0.181,
                              // H1=+0.233 H2=+0.130). Auto-on for crash300 only.

input group "=== Regime Gate (v2.14: M15 BOS) ==="
input bool   UseBOSGate      = true;   // Only fade discharges aligned with the M15 regime
input int    BOSLookback     = 4;      // M15 close must break the N-bar high/low
input int    BOSPersist      = 6;      // Regime stays active N M15 bars after a break

input group "=== Loss Cooldown in Bars (v2.14; E6: -$324/wk on fade — keep 0) ==="
input int    LossCooldownBars = 0;    // Skip entries N bars after a loss (0=off)

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
double g_spikeRatios[];          // Spike range / median at detection (for gate)
double g_lastSpikeRatio = 0;
double g_trailingMaxRatio = 0;  // max adverse spike ratio in trailing window (for solo-core gate)
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

// Actual SL/TP distances attached to position (may differ from median-based)
double g_actualSL = 0;  // Actual SL distance in price units
double g_actualTP = 0;  // Actual TP distance in price units

// Partial entry state
bool   g_partialActive = false;    // Is partial entry mode active?
double g_partialEntryPrice = 0;   // Entry price of first half
double g_partialLotSize = 0;      // Lot size of first half
double g_partialSL = 0;           // SL distance
double g_partialTP = 0;           // TP distance
double g_initialPnL = 0;          // PnL after first bar

// Loss cooldown: spikes remaining to skip after a losing trade
int    g_lossCooldown = 0;
bool   g_blockNextEntry = false;   // This spike was consumed by the cooldown

// v2.14: M15 BOS regime gate + bar-based loss cooldown
int      g_bosM15Dir  = 0;           // Direction of last M15 break: +1 bull, -1 bear, 0 none yet
int      g_bosM15Bars = 999;         // M15 bars since that break
bool     g_lastSpikeGateOK = true;   // Gate snapshot taken at the discharge bar
int      g_lossCooldownBars = 0;     // Bars remaining to skip after a loss
datetime g_lastM15BarTime = 0;       // Forming M15 bar of last regime refresh

// Same-symbol instance lock (global-variable heartbeat). Set only after
// the lock is acquired so a refused instance never deletes the owner's.
string g_lockName = "";

// Warm-up: median range needs ~60 bars before spike math is meaningful
bool   g_warmupDone = false;

// v2.16: parsed cold-hours list + fade-mode runtime config
int    g_coldHours[];
int    g_coldHourCount = 0;
bool   g_fadeMode = true;        // TPGradFactor<=0: bar-close % TP, no intrabar median TP
bool   g_fadeUseCatSL = false;   // CatastropheSLMult > 0
bool   g_useClusterGate = false; // v2.16 flag for the previously always-on cluster gate

// Runtime overrides from TradingSystem preset (copied from input params)
int    g_cfgEntryBar = 1;
int    g_cfgMaxHoldBars = 5;
bool   g_cfgSkipDoubleSpikes = false;
bool   g_cfgUseColdHours = false;

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
ArrayResize(g_spikeRatios, g_spikeMax);
ArrayInitialize(g_spikeRatios, 0);

InitFiles();

// SAME-SYMBOL INSTANCE LOCK: a second EA attached to another chart of
// the SAME symbol would share magic 202603 and double-enter (seen live:
// two ENTRYs 30ms apart on Crash 1000). Lock = GlobalVariable heartbeat;
// refuse to start while a fresh lock exists for this symbol.
string probeLock = "SC2_Instance_" + Symbol();
if(GlobalVariableCheck(probeLock))
{
datetime lastSeen = (datetime)GlobalVariableGet(probeLock);
int age = (int)(TimeLocal() - lastSeen);
if(age < 10)
{
Print("[LOCK] Another SpikeCatcher_v2 is ALREADY running on ", Symbol(),
      " (heartbeat ", age, "s old) — refusing to start to prevent double entries.");
return INIT_FAILED;
}
Print("[LOCK] Stale instance lock (", age, "s old) — taking over.");
}
g_lockName = probeLock;
GlobalVariableSet(g_lockName, (double)TimeLocal());

// Restore ALL of this EA's positions on this symbol (partial entry can
// leave TWO separate positions in hedging mode — both must be managed).
int restored = 0;
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong ticket = PositionGetTicket(i);
if(ticket == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
restored++;
if(restored == 1)   // reference position for display/direction only
{
g_positionDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
g_entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
g_positionTicket = ticket;
}
g_inPosition = true;
}
if(restored > 0)
Print("[RESTART] Restored ", restored, " position(s) @ ~", DoubleToString(g_entryPrice, 3));

g_medianRange = 0.01;

// System presets now ONLY control the filter flags (double-spike skip,
// cold hours). Entry bar and hold length come from the EntryBar and
// MaxHoldBars INPUTS so each chart can run its own verified per-symbol
// config — previously these were hardcoded (1 / 5-3) and the inputs were
// dead code. v2.13 verified entry delays: EntryBar=3 on Crash 1000
// (PF 1.27/1.29 -> 1.45/1.40 OOS), EntryBar=2 on Boom 1000
// (PF 1.30/1.60 -> 1.58/1.80 OOS). Gated 300/500 stay at EntryBar=1.
if(TradingSystem == 1) {
g_cfgSkipDoubleSpikes = false;
Print("=== SYSTEM: MAX PROFIT (double-spike skip off) ===");
} else {
g_cfgSkipDoubleSpikes = true;
Print("=== SYSTEM: MAX SAFETY (double-spike skip on) ===");
}
// v2.16: cold hours are ALWAYS taken from UseColdHours + ColdHoursList
// (the validated edge trades the scattered list; presets no longer
// override it — MAX PROFIT previously disabled cold hours entirely).
g_cfgUseColdHours = UseColdHours;
g_cfgEntryBar = EntryBar;
g_cfgMaxHoldBars = MaxHoldBars;

// v2.16: parse the cold-hours list "1,5,12,14,15,19" -> int array
g_coldHourCount = 0;
ArrayResize(g_coldHours, 0);
string listStr = ColdHoursList;
StringTrimLeft(listStr); StringTrimRight(listStr);
if(StringLen(listStr) > 0)
{
string parts[];
int nParts = StringSplit(listStr, ',', parts);
for(int i = 0; i < nParts; i++)
{
string p = parts[i];
StringTrimLeft(p); StringTrimRight(p);
if(StringLen(p) == 0) continue;
int h = (int)StringToInteger(p);
if(h >= 0 && h <= 23)
{
   ArrayResize(g_coldHours, g_coldHourCount + 1);
   g_coldHours[g_coldHourCount++] = h;
}
}
}
if(g_cfgUseColdHours && g_coldHourCount == 0)
Print("[COLD] ColdHoursList empty/invalid — falling back to LEGACY range ",
   ColdHoursStart, "-", ColdHoursEnd);

g_fadeMode = (TPGradFactor <= 0);
g_fadeUseCatSL = (CatastropheSLMult > 0);
g_useClusterGate = UseClusterGate;

Print("=== SpikeCatcher v2.16 (Phase 1 fade alignment) ===");
if(g_fadeMode)
Print("Fade exits: TP ", DoubleToString(TPPercent, 2), "% of entry on M1 CLOSE | SL: ",
   (SLMultiplier > 0 ? DoubleToString(SLMultiplier, 1) + "x median" : "NONE"),
   " | Cat-SL: ", (g_fadeUseCatSL ? DoubleToString(CatastropheSLMult, 0) + "x median (attached 1.0x)" : "OFF"),
   " | Hold: ", g_cfgMaxHoldBars, " bars | spike_against: CORE");
else
Print("SCALP exits (legacy): TP ", TPGradFactor, "x median intrabar | SL: ",
   (SLMultiplier > 0 ? DoubleToString(SLMultiplier, 1) + "x median" : "NONE"));
Print("Cold Hours: ", (g_cfgUseColdHours ? "ON" : "OFF"),
(g_coldHourCount > 0 ? " list [" + listStr + "] UTC (" + IntegerToString(g_coldHourCount) + " hrs)" : ""));
Print("Tick safety exit: ", UseSafetyExit ? "ON" : "OFF",
" | Cluster gate: ", (g_useClusterGate ? "ON" : "OFF"),
" | Partial: ", (UsePartialEntry ? "ON" : "OFF"),
" | Bar cooldown: ", (LossCooldownBars > 0 ? IntegerToString(LossCooldownBars) : "OFF"));
Print("Safety: ", UseSafetyExit ? "ON" : "OFF", " | Tick threshold: ",
DoubleToString(SafetyMoveMult * (IsIndex1000() ? 1.0 : 0.4), 2),
"x median (", (IsIndex1000() ? "1000" : "300/500"), " calibration)");
Print("Cluster: skip if ", ClusterMin, "+ in ", ClusterWindow, " bars");
Print("Double Spike: ", g_cfgSkipDoubleSpikes ? "ON" : "OFF", " | Cold Hours: ", g_cfgUseColdHours ? "ON" : "OFF");
Print("Partial Entry: ", UsePartialEntry ? "ON (all symbols)" : "OFF");
Print("Loss Cooldown: skip ", LossCooldown, " spikes after a loss", LossCooldown == 0 ? " (OFF)" : "");
Print("Big-Cluster Gate: ", UseBigClusterGate && !IsIndex1000()
? "ON (" + DoubleToString(BigClusterMult,0) + "x within " + IntegerToString(BigClusterWindow) + " bars)"
: (IsIndex1000() ? "OFF (auto — 1000 Index)" : "OFF"));
Print("BOS Gate: ", UseBOSGate
? "ON (M15 close breaks " + IntegerToString(BOSLookback) + "-bar high/low, regime persists " + IntegerToString(BOSPersist) + " M15 bars)"
: "OFF");
Print("Bar Cooldown: ", LossCooldownBars > 0
? "skip entries " + IntegerToString(LossCooldownBars) + " bars after a loss"
: "OFF (spike cooldown: " + IntegerToString(LossCooldown) + ")");
Print("Trailing Max-Ratio Gate: ", (StringFind(StringToLower(Symbol()), "crash300") >= 0 || UseTrailingMaxRatioGate)
? "ON (th=" + DoubleToString(TrailingMaxRatioThreshold,1) + "x within " + IntegerToString(TrailingMaxRatioWindow) + " bars)"
: "OFF (auto — crash300 only)");

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

// Release the instance lock only if WE own it (g_lockName is set only
// after a successful lock acquisition in OnInit)
if(g_lockName != "") GlobalVariableDel(g_lockName);

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

return floorDist * 2.0;  // 2.0x buffer to guarantee first-attempt success
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

// Tick threshold calibrated per symbol: bar medians are ~0.9-1.0 on
// the 300/500s vs ~0.36 on the 1000s, so a fixed 2.5x let multi-tick
// medium spikes slip through on the wide-median charts. Effective:
//   300/500: 2.5 x 0.4 = 1.0 x median range in ONE tick (a whole
//           normal minute in a single tick = genuinely abnormal)
//   1000:    2.5 x 1.0 = unchanged (was working live)
double spikeMult = IsIndex1000() ? 1.0 : 0.4;
if(g_tickMove > SafetyMoveMult * spikeMult * g_medianRange)
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

// v2.14: per-bar upkeep — bar-cooldown countdown + M15 regime refresh
if(g_lossCooldownBars > 0) g_lossCooldownBars--;
UpdateBOSRegime();

// Warm-up: median of the 100-bar window is unreliable until ~60 real
// bars exist. Entries stay disabled until then (see CheckEntry).
if(!g_warmupDone && g_rangeCount >= 60)
{
g_warmupDone = true;
Print("[WARMUP] Median warm (", g_rangeCount, " bars, median=",
   DoubleToString(g_medianRange, 4), ") — entries enabled");
}

bool isSpike = (barRange > SpikeThreshold * g_medianRange);
bool isCrashSpike = isSpike && (c < o);
bool isBoomSpike = isSpike && (c > o);
bool isMySpike = IsCrash() ? isCrashSpike : isBoomSpike;

if(isMySpike && g_medianRange > 0)
{
g_lastSpikeBar = g_barCount;
g_barsSinceSpike = 0;
g_requiresNewSpike = false;
g_lastSpikeGateOK = CheckBOSGate();   // v2.14: regime snapshot at the discharge bar
g_spikes[g_spikeCount % g_spikeMax] = g_barCount;
g_lastSpikeRatio = barRange / g_medianRange;
g_spikeRatios[g_spikeCount % g_spikeMax] = g_lastSpikeRatio;
g_spikeCount++;
Print("[SPIKE #", g_spikeCount, "] Bar ", g_barCount,
   " Range=", DoubleToString(barRange,5),
   " Median=", DoubleToString(g_medianRange,5),
   " Ratio=", DoubleToString(barRange/g_medianRange,1), "x");

// LOSS COOLDOWN: consume the cooldown on each new spike and tag
// that spike so its entry opportunity (1 bar later) is skipped too
if(g_lossCooldown > 0)
{
g_lossCooldown--;
g_blockNextEntry = true;
if(g_lossCooldown == 0)
   Print("[COOLDOWN] This is the last skipped spike — entries resume after it");
}

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

// v2.16 FADE BAR-CLOSE MANAGEMENT — runs BEFORE any legacy intrabar
// checks, in the validated executor's reference order: TP on close,
// then same-direction spike-against, then timeout.
if(g_fadeMode)
{
double closeMove = (g_positionDir == 1) ? (c - g_entryPrice)
                                          : (g_entryPrice - c);
if(closeMove >= g_entryPrice * TPPercent / 100.0)
{
   double pnl = CalculateCurrentPnL(c);
   ClosePosition("tp", pnl);
   if(ShowInfo) UpdatePanel();
   return;
}
// Same-direction spike against the fade = exit (CORE in fade mode,
// decoupled from UseSafetyExit which is the TICK-level exit)
bool spikeAgainstMe = false;
if(g_positionDir == 1 && isCrashSpike)  spikeAgainstMe = true;  // long, new DOWN spike
if(g_positionDir == -1 && isBoomSpike)  spikeAgainstMe = true;  // short, new UP spike
if(spikeAgainstMe)
{
   double pnl = CalculateCurrentPnL(c);
   ClosePosition("spike_against", pnl);
   if(ShowInfo) UpdatePanel();
   return;
}
}

// PARTIAL ENTRY: after 1 bar, check if profitable and add second half
if(g_partialActive && g_barsHeld == 1)
{
double currentPnL = CalculateCurrentPnL(c);
g_initialPnL = currentPnL;

if(currentPnL > 0)
{
   // WINNING! Add second 50%
   Print("[PARTIAL] Bar 1 profitable ($", DoubleToString(currentPnL, 4), ") — adding second half");

   double secondLot = g_partialLotSize;  // Same size as first half
   bool addSuccess = false;
   if(g_positionDir == 1)
      addSuccess = OpenBuy(secondLot, g_partialTP, g_partialSL);
   else
      addSuccess = OpenSell(secondLot, g_partialTP, g_partialSL);

   if(addSuccess)
   {
      // Attach stops to the new position
      AttachStops(g_positionDir, g_partialTP, g_partialSL, g_positionTicket);
      Print("[PARTIAL] Second half added. Total position: ", DoubleToString(g_partialLotSize * 2, 2), " lots");
      LogTrade("PARTIAL_ADD", (g_positionDir == 1 ? "LONG" : "SHORT"), 
               g_entryPrice, 0, 0, "partial_add", secondLot);
   }
   else
   {
      Print("[PARTIAL] Failed to add second half — continuing with first half only");
   }

   g_partialActive = false;  // Done with partial logic
}
else
{
   // LOSING! Don't add (cluster protection)
   Print("[PARTIAL] Bar 1 losing ($", DoubleToString(currentPnL, 4), ") — NOT adding second half (cluster protection)");
   g_partialActive = false;  // Done with partial logic
}
}

if(CheckExitLevels(rates))
return;

if(g_barsHeld >= g_cfgMaxHoldBars)
{
double pnl = CalculateCurrentPnL(c);
ClosePosition("timeout", pnl);
}
}
else
{
if(!g_inPosition && g_barsSinceSpike == g_cfgEntryBar)
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
if(g_barsSinceSpike != g_cfgEntryBar) return;
if(g_lastSpikeBar < 0) return;
if(g_medianRange <= 0) return;
if(!g_warmupDone) return;   // median needs >=60 bars before it is meaningful

// LOSS COOLDOWN: losses cluster on Boom/Crash — sit out the next
// few spikes after a loss instead of re-entering into a cluster.
// g_blockNextEntry tags the spike that consumed the last cooldown
// unit, so it also skips that spike's entry opportunity.
if(g_blockNextEntry)
{
g_blockNextEntry = false;
Print("[SKIP] Loss cooldown spike — waiting for a fresh spike");
return;
}
if(g_lossCooldown > 0 && LossCooldown > 0)
{
Print("[SKIP] Loss cooldown: ", g_lossCooldown, " spike(s) remaining");
return;
}

// v2.14: BAR-BASED LOSS COOLDOWN — losses cluster in bars, not spikes.
// Sim: skipping 10 bars after a loss ~doubles EV vs the spike-count skip.
if(LossCooldownBars > 0 && g_lossCooldownBars > 0)
{
Print("[SKIP] Bar cooldown: ", g_lossCooldownBars, " bar(s) remaining");
return;
}

// SKIP DOUBLE SPIKES: if previous spike was only 1-3 bars ago, skip
if(g_cfgSkipDoubleSpikes && g_spikeCount >= 2)
{
int prevSpikeBar = g_spikes[(g_spikeCount - 2) % g_spikeMax];
int interval = g_lastSpikeBar - prevSpikeBar;
if(interval >= 1 && interval <= DoubleSpikeWindow)
{
Print("[SKIP] Double spike risk: interval=", interval, " bars");
return;
}
}

// COLD HOURS (v2.16): scattered UTC hour list — the validated edge's
// window [1,5,12,14,15,19]. TimeGMT() because server TimeCurrent() is
// not UTC on Deriv and the backtests are UTC-defined.
if(g_cfgUseColdHours)
{
MqlDateTime dt;
TimeGMT(dt);
int hour = dt.hour;
bool inCold = false;
if(g_coldHourCount > 0)
{
for(int i = 0; i < g_coldHourCount; i++)
   if(g_coldHours[i] == hour) { inCold = true; break; }
}
else // LEGACY range fallback (ColdHoursList empty)
{
if(ColdHoursStart < ColdHoursEnd)
   inCold = (hour >= ColdHoursStart && hour < ColdHoursEnd);
else
   inCold = (hour >= ColdHoursStart || hour < ColdHoursEnd);
}
if(!inCold)
{
Print("[SKIP] Not COLD hour (UTC): ", hour);
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
// v2.16: gate is now optional (default OFF — untested on the fade)
if(g_useClusterGate && clusterCount >= ClusterMin - 1) return;

if(UseTrendFilter && !CheckTrend()) return;    if(g_requiresNewSpike) return;

// v2.14: M15 BOS REGIME GATE — only fade the discharge when the M15
// structure agrees with the drift direction (snapshot at discharge bar).
if(UseBOSGate && !g_lastSpikeGateOK)
{
string rs = (g_bosM15Dir > 0) ? "BULL" : (g_bosM15Dir < 0) ? "BEAR" : "NONE";
Print("[SKIP] BOS gate: M15 regime ", rs, " (age ", g_bosM15Bars,
   " bars) against drift — waiting for alignment");
return;
}

// BIG-CLUSTER GATE: only fade spikes that are BIG (>= BigClusterMult x
// median) AND part of a tight group of big spikes (another >= BigClusterMult
// within BigClusterWindow bars). Verified on the corrected backtest engine:
// PF 1.36->1.71 / 1.37->1.74 / 1.36->1.63 (OOS, crash300/500, boom500).
// Auto-disabled on the 1000 Index symbols where the gate does NOT hold.
if(!BigGatePassed())
{
Print("[SKIP] Big-cluster gate: spike ",
   DoubleToString(g_lastSpikeRatio,1), "x ",
   (IsIndex1000() ? "(1000 Index — gate off, this should not print)" : "not big/clustered enough"));
return;
}

// TRAILING MAX-RATIO GATE (solo-core filter, v2.15):
// Skip entry if the largest adverse spike in the last TrailingMaxRatioWindow
// bars (excluding the current spike) had a ratio >= Threshold. Cluster starts
// are preceded by big spikes; solos occur in quiet periods. Verified OOS on
// corrected engine: crash300 th=8.0 -> diff +0.181 (H1=+0.233, H2=+0.130,
// n=2220, 32% kept). Auto-enabled for crash300 only.
if(!TrailingMaxRatioGatePassed())
{
Print("[SKIP] Trailing max-ratio gate: max recent ratio=",
   DoubleToString(g_trailingMaxRatio,1), "x >=",
   DoubleToString(TrailingMaxRatioThreshold,1), "x threshold");
return;
}

int dir = IsCrash() ? 1 : -1;

// v2.16 fade sizing: denominator FIXED at 1.5x median — the validated
// parity basis (docs/REPLAY_VS_BACKTEST_GAP.md). Per-trade stops are NOT
// the risk model (docs/TAIL_RISK_STOP_LOSSES.md: size to the tail, never
// stop into it); account protection = optional catastrophe SL far outside
// the spike tail + RiskPercent itself.
double sizingDist = 1.5 * g_medianRange;

double lotSize = CalculateLotSize(sizingDist);
if(lotSize <= 0) {
Print("[SKIP] Lot size too small. sizingDist=", DoubleToString(sizingDist,5),
   " Median=", DoubleToString(g_medianRange,5),
   " Equity=$", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY),2));
return;
}

// v2.16: stop/TP distances for the entry request.
// Fade mode: NO SL unless CatastropheSLMult is set (attached at 1.0x so it
// sits where intended — it is margin insurance outside the spike tail, not
// the risk model). TP distance feeds the broker-attached backup TP; the
// REAL fade TP is the 0.4% bar-close check managed in OnNewBar.
// Scalp mode (TPGradFactor>0): legacy median-multiplier geometry.
double refPrice = (dir == 1) ? SymbolInfoDouble(Symbol(), SYMBOL_ASK)
                        : SymbolInfoDouble(Symbol(), SYMBOL_BID);
double tp = 0, sl = 0;
if(g_fadeMode)
{
tp = refPrice * TPPercent / 100.0;
if(SLMultiplier > 0)
sl = SLMultiplier * g_medianRange;          // explicit nonstandard fade SL
else if(g_fadeUseCatSL)
sl = CatastropheSLMult * g_medianRange;     // account-protection only
// else sl stays 0 -> no stop attached (fade semantics)
}
else
{
tp = TPMultiplier * g_medianRange;
sl = SLMultiplier * g_medianRange;
if(sl > 0)
{
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
}
}

// PARTIAL ENTRY: enter 50% now, add 50% after 1 bar if winning.
// Corrected M1 backtest (real gap fills, no lookahead) shows partial
// helps BOOM as much as CRASH: PF 0.81-1.00 full -> 1.15-1.39 partial
// on all five symbols, both out-of-sample halves.
bool usePartial = UsePartialEntry;
double entryLot = lotSize;
if(usePartial) {
entryLot = lotSize / 2.0;
// Round to lot step
double lotStep = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_STEP);
entryLot = MathFloor(entryLot / lotStep) * lotStep;
double minLot = SymbolInfoDouble(Symbol(), SYMBOL_VOLUME_MIN);
entryLot = MathMax(entryLot, minLot);
Print("[PARTIAL] Entering 50%: ", DoubleToString(entryLot, 2), " lots (full would be ", DoubleToString(lotSize, 2), ")");
}

double point = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

// FINAL SAFETY: check for existing positions (prevents duplicate entries)
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong ticket = PositionGetTicket(i);
if(ticket == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
Print("[SKIP] Existing position found — preventing duplicate entry");
return;
}

bool success = false;
if(dir == 1)
success = OpenBuy(entryLot, tp, sl);
else
success = OpenSell(entryLot, tp, sl);

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
g_actualSL = sl;                    // fade: cat-SL distance (0 = none)
g_actualTP = g_fadeMode ? 0 : tp;   // fade: 0 disables intrabar TP (bar-close-managed)

// Set up partial entry state
if(usePartial) {
g_partialActive = true;
g_partialEntryPrice = g_entryPrice;
g_partialLotSize = entryLot;
g_partialSL = sl;
g_partialTP = tp;
g_initialPnL = 0;
Print("[PARTIAL] First half entered. Waiting 1 bar to check if adding second half.");
}

Print("[ENTRY] ", (dir == 1 ? "LONG" : "SHORT"),
   " @ ", DoubleToString(g_entryPrice, digits),
   " TP=", DoubleToString(tp, digits),
   " SL=", DoubleToString(sl, digits),
   " Lots=", DoubleToString(entryLot, 2),
   (usePartial ? " (PARTIAL 50%)" : ""));

LogTrade("OPEN", (dir == 1 ? "LONG" : "SHORT"), g_entryPrice, 0, 0, 
      usePartial ? "partial_entry" : "entry", entryLot);

// v2.16: the fade is software-managed (bar-close TP / spike_against /
// timeout) — no broker TP backup, because an auto-TP at any multiple
// would exit at prices the validated strategy holds through. Attach
// broker-side protection ONLY when a catastrophe SL is configured.
// AttachStops (SL+TP at 1.5x/2.5x) remains for legacy scalp mode.
if(g_fadeMode)
{
if(sl > 0 && !AttachFadeSL(dir, sl, g_positionTicket))
{
   Print("[NO STOPS] Catastrophe SL attach failed — closing for safety");
   double exitPrice = (dir == 1) ?
      SymbolInfoDouble(Symbol(), SYMBOL_BID) :
      SymbolInfoDouble(Symbol(), SYMBOL_ASK);
   double pnl = CalculateCurrentPnL(exitPrice);
   ClosePosition("no_stops", pnl);
}
}
else if(!AttachStops(dir, tp, sl, g_positionTicket))
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
double tp = g_actualTP;  // Use ACTUAL TP attached to position (0 = none in fade mode)
double sl = g_actualSL;  // Use ACTUAL SL attached to position (0 = none in fade mode)

// v2.16: zero distance = that exit does not exist (fade mode runs with
// no TP/SL here — exits are bar-close-managed in OnNewBar)
bool hitTP = false;
bool hitSL = false;

if(g_positionDir == 1)
{
hitTP = (tp > 0) && ((h - g_entryPrice) >= tp);
hitSL = (sl > 0) && ((g_entryPrice - l) >= sl);
}
else
{
hitTP = (tp > 0) && ((g_entryPrice - l) >= tp);
hitSL = (sl > 0) && ((h - g_entryPrice) >= sl);
}

if(!hitTP && !hitSL) return false;   // v2.16: nothing attached -> broker levels cannot exit

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
// Broker-computed unrealized PnL across ALL of this EA's positions on
// this symbol (POSITION_PROFIT). Handles partial-entry second halves
// correctly — they are separate positions in hedging mode, each with its
// own entry price, so distance-based math from one reference is wrong.
double CalculateCurrentPnL(double currentPrice)
{
double total = 0;
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong ticket = PositionGetTicket(i);
if(ticket == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
total += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
}
return total;
}

//+------------------------------------------------------------------+
//| M15 break-of-structure regime (v2.14)                              |
//| Refreshed once per M1 bar but derived only from CLOSED M15 bars,   |
//| so the state is stable within each M15 period (no lookahead).      |
//+------------------------------------------------------------------+
void UpdateBOSRegime()
{
if(!UseBOSGate) return;
MqlRates r[];
int need = BOSLookback + 2;
if(CopyRates(Symbol(), PERIOD_M15, 0, need, r) < need) return;

// Refresh once per NEW M15 bar — the age counter must count M15 bars,
// and the state only changes when a bar closes.
datetime curM15 = r[need - 1].time;
if(curM15 == g_lastM15BarTime) return;
g_lastM15BarTime = curM15;

int t = need - 2;                       // last CLOSED M15 bar
double c  = r[t].close;
double hh = -DBL_MAX, ll = DBL_MAX;
for(int k = t - BOSLookback; k < t; k++)
{
if(k < 0) continue;
if(r[k].high > hh) hh = r[k].high;
if(r[k].low  < ll) ll = r[k].low;
}
if(c > hh)      { g_bosM15Dir = 1;  g_bosM15Bars = 0; }
else if(c < ll) { g_bosM15Dir = -1; g_bosM15Bars = 0; }
else if(g_bosM15Bars < 999) g_bosM15Bars++;
}

//+------------------------------------------------------------------+
//| Gate: does the current M15 regime match the drift direction?       |
//| Sim semantics: last break within BOSPersist bars wins; the break   |
//| must be in the drift direction (bull=crash, bear=boom).            |
//+------------------------------------------------------------------+
bool CheckBOSGate()
{
if(!UseBOSGate) return true;                 // gate disabled -> pass
if(g_bosM15Dir == 0) return false;           // no structure break seen yet
if(g_bosM15Bars >= BOSPersist) return false; // regime expired
bool driftBull = IsCrash();                  // crash drifts up between crashes
return (g_bosM15Dir == 1) == driftBull;
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
//+------------------------------------------------------------------+
//| v2.16: attach ONLY a catastrophe SL (no TP) for fade mode.          |
//| Attached at 1.0x the configured distance so it sits where intended; |
//| distance must exceed the spike tail (default 80x median ≈ 55% of    |
//| price on the 1000s) — fill-through beyond p99 is accepted; this is  |
//| margin insurance, not the strategy's risk model.                    |
//+------------------------------------------------------------------+
bool AttachFadeSL(int dir, double slDist, ulong targetTicket)
{
ulong ticket = targetTicket;
if(ticket == 0 || !PositionSelectByTicket(ticket))
{
Print("[SLTP] No open position for catastrophe SL attach");
return false;
}

double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
double slPrice = (dir == 1) ? openPrice - slDist : openPrice + slDist;

MqlTradeRequest request = {};
MqlTradeResult result = {};
request.action   = TRADE_ACTION_SLTP;
request.position = ticket;
request.symbol   = Symbol();
request.sl       = NormalizeDouble(slPrice, digits);
request.tp       = 0;

for(int attempt = 0; attempt < 2; attempt++)
{
if(OrderSend(request, result) && result.retcode == TRADE_RETCODE_DONE)
{
Print("[SLTP] Catastrophe SL attached at ", DoubleToString(request.sl, digits),
      " (", DoubleToString(slDist / g_medianRange, 0), "x median — margin insurance only)");
return true;
}
Print("[SLTP FAIL] cat-SL attempt ", attempt + 1, " Error: ", result.retcode, " ", result.comment);
}
return false;
}

bool AttachStops(int dir, double tpDist, double slDist, ulong targetTicket = 0)
{
ulong ticket = 0;
if(targetTicket != 0)
{
// Attach to the exact position just opened. Partial adds create a
// SECOND position in hedging mode — blindly picking "first found"
// could re-attach the first half and leave the new half with no stops.
if(!PositionSelectByTicket(targetTicket)) return false;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) return false;
if(PositionGetInteger(POSITION_MAGIC) != 202603) return false;
ticket = targetTicket;
}
else
{
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong t = PositionGetTicket(i);
if(t == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
ticket = t;
break;
}
}
if(ticket == 0)
{
Print("[SLTP] No open position found to attach stops to");
return false;
}

for(int attempt = 0; attempt < 2; attempt++)
{
double mult = (attempt == 0) ? 1.5 : 2.5;  // Start at 1.5x to ensure first-attempt success

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
//| Close position — with retry for reliability                        |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Close ALL positions of this EA on this symbol. Partial entry      |
//| opens TWO separate positions (hedging mode); closing only one      |
//| orphaned the other with no timeout/safety management, and the EA's |
//| per-trade PnL prints never matched the account. Stats now use the  |
//| broker's REALIZED PnL from each closing deal.                      |
//+------------------------------------------------------------------+
bool ClosePosition(string reason, double pnl)
{
// Snapshot current tickets (positions may vanish mid-loop if the
// broker's own SL/TP fires first)
ulong tickets[];
int n = 0;
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong ticket = PositionGetTicket(i);
if(ticket == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
n++;
ArrayResize(tickets, n);
tickets[n-1] = ticket;
}
if(n == 0) return false;

double cyclePnl = 0;
int    closedCount = 0;
int    winCount = 0;
int    lossCount = 0;

for(int k = 0; k < n; k++)
{
ulong ticket = tickets[k];
bool  closed = false;

for(int retry = 0; retry < 3 && !closed; retry++)
{
if(!PositionSelectByTicket(ticket))
{
   closed = true;   // broker SL/TP already closed it — account for it
   break;
}

MqlTradeRequest request = {};
MqlTradeResult result = {};

int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);

request.action = TRADE_ACTION_DEAL;
request.symbol = Symbol();
request.volume = PositionGetDouble(POSITION_VOLUME);
request.position = ticket;
// During spikes, price moves fast — use MUCH wider deviation
// to ensure the close order fills instead of being rejected (10029)
request.deviation = (reason == "safety_spike") ? 3000 : 500;
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
if(sent && result.retcode == TRADE_RETCODE_DONE)
{
   closed = true;
   break;
}

Print("[CLOSE FAIL] ", reason, " Error: ", result.retcode, " retry ", retry+1,
      " ticket=", IntegerToString(ticket));
if(retry < 2) Sleep((reason == "safety_spike") ? 50 : 100);
}

if(!closed) continue;   // still open after retries — keep managing it

// Realized PnL + prices straight from the broker's deal history
int    tDir = 0;
double tEntry = 0, tFill = 0, tPnl = 0, tVol = 0;
GetTicketInfo(ticket, tDir, tEntry, tFill, tPnl, tVol);
LogCalibration(ticket, tDir, tEntry);
LogTrade("CLOSE", (tDir == 1 ? "LONG" : "SHORT"), tEntry, tFill, tPnl, reason, tVol);

cyclePnl += tPnl;
if(tPnl > 0) winCount++; else lossCount++;
closedCount++;
}

if(closedCount == 0) return false;

g_totalTrades += closedCount;
g_wins        += winCount;
g_losses      += lossCount;
g_totalPnL    += cyclePnl;

// LOSS COOLDOWN: losing trades cluster — after a loss, skip the
// next LossCooldown spikes to dodge double-spike follow-ups
if(cyclePnl <= 0 && LossCooldown > 0)
{
g_lossCooldown = LossCooldown;
Print("[COOLDOWN] Loss of $", DoubleToString(cyclePnl, 4),
   " — skipping next ", LossCooldown, " spike(s)");
}

// v2.14: bar-based cooldown arms on the same realized loss
if(cyclePnl <= 0 && LossCooldownBars > 0)
{
g_lossCooldownBars = LossCooldownBars;
Print("[COOLDOWN] Loss of $", DoubleToString(cyclePnl, 4),
   " — skipping entries for ", LossCooldownBars, " bar(s)");
}

double wr = g_totalTrades > 0 ? (double)g_wins/g_totalTrades*100 : 0;
Print("[", reason, "] PnL=$", DoubleToString(cyclePnl, 4),
" (", closedCount, " position", (closedCount > 1 ? "s" : ""), ") | ",
g_totalTrades, "T WR=", DoubleToString(wr, 1), "%",
" PnL=$", DoubleToString(g_totalPnL, 4),
" Safety=", g_safetyExits);

if(CountMyPositions() == 0)
{
g_inPosition = false;
g_requiresNewSpike = true;
g_positionDir = 0;
g_entryPrice = 0;
g_barsHeld = 0;
g_positionTicket = 0;
g_actualSL = 0;
g_actualTP = 0;
g_partialActive = false;
g_partialEntryPrice = 0;
g_partialLotSize = 0;
g_partialSL = 0;
g_partialTP = 0;
g_initialPnL = 0;
}
else
{
// Some positions remain (close failed) — keep managing them
RefreshPositionState();
Print("[CLOSE] ", CountMyPositions(), " position(s) remain open — continuing to manage");
}

return true;
}

//+------------------------------------------------------------------+
//| Count this EA's open positions on this symbol                     |
//+------------------------------------------------------------------+
int CountMyPositions()
{
int cnt = 0;
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong ticket = PositionGetTicket(i);
if(ticket == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
cnt++;
}
return cnt;
}

//+------------------------------------------------------------------+
//| Refresh display state from the newest surviving position          |
//+------------------------------------------------------------------+
void RefreshPositionState()
{
for(int i = PositionsTotal() - 1; i >= 0; i--)
{
ulong ticket = PositionGetTicket(i);
if(ticket == 0) continue;
if(PositionGetString(POSITION_SYMBOL) != Symbol()) continue;
if(PositionGetInteger(POSITION_MAGIC) != 202603) continue;
g_positionDir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
g_entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
g_positionTicket = ticket;
g_inPosition = true;
return;
}
g_inPosition = false;
}

//+------------------------------------------------------------------+
//| One position's realized PnL / entry / fill / direction from the   |
//| broker's deal history. Works even after the position is closed.   |
//+------------------------------------------------------------------+
bool GetTicketInfo(ulong ticket, int &dir, double &entry, double &fill, double &pnl,
            double &vol)
{
dir = 0; entry = 0; fill = 0; pnl = 0; vol = 0;
if(ticket == 0) return false;
if(!HistorySelectByPosition(ticket)) return false;
for(int i = 0; i < HistoryDealsTotal(); i++)
{
ulong deal = HistoryDealGetTicket(i);
if(deal == 0) continue;
if(HistoryDealGetString(deal, DEAL_SYMBOL) != Symbol()) continue;
if(HistoryDealGetInteger(deal, DEAL_MAGIC) != 202603) continue;
if(HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_IN)
{
if(entry == 0)
{
   entry = HistoryDealGetDouble(deal, DEAL_PRICE);
   dir = (HistoryDealGetInteger(deal, DEAL_TYPE) == DEAL_TYPE_BUY) ? 1 : -1;
}
}
else if(HistoryDealGetInteger(deal, DEAL_ENTRY) == DEAL_ENTRY_OUT)
{
fill = HistoryDealGetDouble(deal, DEAL_PRICE);
vol  = HistoryDealGetDouble(deal, DEAL_VOLUME);
pnl += HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP);
}
}
return (dir != 0);
}

//+------------------------------------------------------------------+
//| GROUND-TRUTH $/point/lot CALIBRATION                              |
//| Reads the broker's ACTUAL closing deal for the position (realized  |
//| profit + swap, real fill price, real volume) and derives $ per 1.0 |
//| index point per 1.0 lot. This is the only number that validates   |
//| the PnL projections: the EA's estimated PnL uses                 |
//| SYMBOL_TRADE_TICK_VALUE, which is exactly what we're verifying,   |
//| so it cannot cross-check itself. NOTE: partial-entry second halves |
//| add volume at a slightly different price, so a few rows carry a    |
//| few-% bias — SL-gap and timeout closes are the truest.             |
//+------------------------------------------------------------------+
void LogCalibration(ulong ticket, int tDir, double tEntry)
{
if(ticket == 0) return;
if(!HistorySelectByPosition(ticket)) return;

for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
{
ulong deal = HistoryDealGetTicket(i);
if(deal == 0) continue;
if(HistoryDealGetInteger(deal, DEAL_ENTRY) != DEAL_ENTRY_OUT) continue;
if(HistoryDealGetString(deal, DEAL_SYMBOL) != Symbol()) continue;
if(HistoryDealGetInteger(deal, DEAL_MAGIC) != 202603) continue;

double realPnl   = HistoryDealGetDouble(deal, DEAL_PROFIT) +
                  HistoryDealGetDouble(deal, DEAL_SWAP);
double fillPrice = HistoryDealGetDouble(deal, DEAL_PRICE);
double vol       = HistoryDealGetDouble(deal, DEAL_VOLUME);
double move      = MathAbs(fillPrice - tEntry);
double perPtLot  = (move > 0 && vol > 0) ? realPnl / (move * vol) : 0;

int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
Print("[CALIB] ", Symbol(), " ", (tDir == 1 ? "LONG" : "SHORT"),
   " lots=", DoubleToString(vol, 2),
   " entry=", DoubleToString(tEntry, digits),
   " fill=", DoubleToString(fillPrice, digits),
   " move=", DoubleToString(move, 3),
   " realPnL=", DoubleToString(realPnl, 2),
   " => $/pt/lot = ", DoubleToString(perPtLot, 4));
return;
}
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
//| 1000 Index symbols? The big-cluster gate was verified on the 300/
//| 500 series only and HURTS the 1000s (OOS) — auto-disable there.   |
//+------------------------------------------------------------------+
bool IsIndex1000()
{
string name = Symbol();
StringToLower(name);
return StringFind(name, "1000") >= 0;
}

//+------------------------------------------------------------------+
//| BIG-CLUSTER GATE                                                  |
//| Current spike must be >= BigClusterMult x median AND have another  |
//| spike >= BigClusterMult x median within BigClusterWindow bars.     |
//| Returns true = allow entry (or gate not applicable).               |
//+------------------------------------------------------------------+
bool BigGatePassed()
{
if(!UseBigClusterGate) return true;
if(IsIndex1000()) return true;            // verified: 300/500 only
if(g_spikeCount < 2) return false;        // need a prior spike to form a group
if(g_lastSpikeRatio < BigClusterMult) return false;

for(int i = 0; i < g_spikeCount; i++)
{
int bar = g_spikes[i % g_spikeMax];     // ring buffer (matches writes)
if(bar == g_lastSpikeBar) continue;     // the current spike itself
if(g_lastSpikeBar - bar > BigClusterWindow) continue;  // too old
if(g_spikeRatios[i % g_spikeMax] >= BigClusterMult)
return true;
}
return false;
}

//+------------------------------------------------------------------+
//| TRAILING MAX-RATIO GATE (solo-core filter, v2.15)                 |
//| Scan the ring buffer for adverse spikes in the last N bars.       |
//| If the largest ratio among them >= Threshold, skip entry.         |
//| Auto-enabled for crash300 only.                                   |
//+------------------------------------------------------------------+
bool TrailingMaxRatioGatePassed()
{
// Auto-on for crash300 (verified OOS: th=8.0, diff +0.181, H1=+0.233, H2=+0.130).
// UseTrailingMaxRatioGate=false forces OFF even on crash300 (manual override).
string sym = Symbol();
StringToLower(sym);
bool isCrash300 = (StringFind(sym, "crash") >= 0 && StringFind(sym, "300") >= 0);
bool enabled = isCrash300 ? true : UseTrailingMaxRatioGate;
if(!enabled) return true;
if(g_spikeCount < 1) return true;

double maxRatio = 0;
int windowBars = TrailingMaxRatioWindow;
for(int i = 0; i < g_spikeCount; i++)
{
int bar = g_spikes[i % g_spikeMax];
if(bar == g_lastSpikeBar) continue;            // exclude current spike (test does too)
if(g_lastSpikeBar - bar > windowBars) continue; // too old
double r = g_spikeRatios[i % g_spikeMax];
if(r > maxRatio) maxRatio = r;
}
g_trailingMaxRatio = maxRatio;
if(maxRatio >= TrailingMaxRatioThreshold) return false;
return true;
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
            "pnl","reason","bars_held","equity","tp_mult","sl_mult","volume");
FileClose(h);
}
}
}

void LogTrade(string action, string direction, double entry, double exit,
      double pnl, string reason, double volume = 0)
{
int h = FileOpen(g_logFile, FILE_COMMON|FILE_WRITE|FILE_CSV, ',');
if(h == INVALID_HANDLE) return;
FileSeek(h, 0, SEEK_END);

string time = TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS);
FileWrite(h, time, Symbol(), action, direction,
      DoubleToString(entry, 5), DoubleToString(exit, 5),
      DoubleToString(pnl, 4), reason, g_barsHeld,
      DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2),
      DoubleToString(TPMultiplier, 2), DoubleToString(SLMultiplier, 2),
      DoubleToString(volume, 2));
FileClose(h);
}

//+------------------------------------------------------------------+
//| Chart panel                                                        |
//+------------------------------------------------------------------+
void OnTimer()
{
if(ShowInfo) UpdatePanel();

// Instance-lock heartbeat: tells other charts of the same symbol that
// this EA instance is alive (value = last heartbeat time)
if(g_lockName != "") GlobalVariableSet(g_lockName, (double)TimeLocal());
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
CreateLabel(p+"t1", x, y, "SPIKE CATCHER v2.13 [" + sysName + "] — " + Symbol(), sysClr, 10); y+=20;
CreateLabel(p+"t2", x, y, "Price: "+DoubleToString(price,3)+" Equity: $"+DoubleToString(equity,2), clrWhite, 9); y+=18;
CreateLabel(p+"t3", x, y, "Median: "+DoubleToString(g_medianRange,4)+" Bars: "+IntegerToString(g_barCount), clrWhite, 9); y+=18;

string posStr = "FLAT";
color posClr = clrWhite;
if(g_inPosition)
{
string partialTag = g_partialActive ? " [WAITING 2ND HALF]" : "";
posStr = (g_positionDir==1?"LONG":"SHORT")+" @ "+DoubleToString(g_entryPrice,3)+" ("+IntegerToString(g_barsHeld)+" bars)"+partialTag;
posClr = g_positionDir==1 ? clrLime : clrRed;
}
CreateLabel(p+"t4", x, y, "Pos: "+posStr, posClr, 9); y+=18;
string partialInfo = UsePartialEntry ? "Partial: ON" : "Partial: OFF";
string coolInfo = (g_lossCooldown > 0) ? " Cool: "+IntegerToString(g_lossCooldown)+" spikes" : "";
CreateLabel(p+"t5", x, y, "Trades: "+IntegerToString(g_totalTrades)+" WR: "+DoubleToString(wr,1)+"% PnL: $"+DoubleToString(g_totalPnL,4)+" | "+partialInfo+coolInfo, clrWhite, 9); y+=18;
CreateLabel(p+"t6", x, y, "Safety Exits: "+IntegerToString(g_safetyExits)+" Spikes Detected: "+IntegerToString(g_spikesDetected), clrYellow, 9); y+=18;

// EXPOSURE METER: P(adverse spike lands in trade window) from the
// memoryless spike process. Exposure = remaining max hold bars (or
// the full window when flat). P = 1 - exp(-exposure_min / mean_gap_min)
double meanGap = GetMeanSpikeGapMin();
int maxHold = g_cfgMaxHoldBars;
if(maxHold <= 0) maxHold = 7;
int exposureMin = maxHold;
string windowNote = "(next " + IntegerToString(maxHold) + "m if entered now)";
if(g_inPosition)
{
// In a trade: exposure = remaining bars until the timeout exit
exposureMin = MathMax(1, maxHold - g_barsHeld);
windowNote = "(remaining " + IntegerToString(exposureMin) + "m in trade)";
}
double pSpike = 1.0 - MathExp(-(double)exposureMin / meanGap);
color expClr = clrLime;
if(pSpike > 0.30) expClr = clrYellow;
if(pSpike > 0.50) expClr = clrRed;
CreateLabel(p+"t7", x, y, "Spike exposure: P(adverse spike " + windowNote + ") = " +
      DoubleToString(pSpike*100, 0) + "%  [mean gap " + DoubleToString(meanGap, 1) + "m]", expClr, 9);
}

//+------------------------------------------------------------------+
//| Mean time between ADVERSE spikes (measured from M1 history)        |
//| The spike timer is memoryless: P(spike in next t min) = 1-e^(-t/g) |
//+------------------------------------------------------------------+
double GetMeanSpikeGapMin()
{
string name = Symbol();
StringToLower(name);
if(StringFind(name, "crash") >= 0)
{
if(StringFind(name, "300") >= 0) return 8.1;   // Crash 300
if(StringFind(name, "500") >= 0) return 11.1;  // Crash 500
return 19.5;                                     // Crash 1000
}
else
{
if(StringFind(name, "500") >= 0) return 11.1;   // Boom 500
return 19.7;                                     // Boom 1000 / other
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

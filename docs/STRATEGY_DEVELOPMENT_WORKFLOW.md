# Strategy Development Workflow
## Claude Code (Proposes) → Buffy (Verifies) → Deploy (If Proven)

**Date:** September 1, 2026
**Purpose:** Establish checks and balances for strategy development

---

## The Two-System Approach

| Role | Agent | Responsibility |
|------|-------|----------------|
| **Strategy Developer** | Claude Code | Propose strategies, write MD docs, suggest changes |
| **Verification Agent** | Buffy | Backtest, validate, implement, flag issues |

---

## How It Works

### Step 1: Claude Code Proposes Strategy

Claude Code creates a strategy document with:
- Hypothesis (what the strategy claims)
- Entry/exit rules
- Expected performance
- Implementation plan

**Output:** New `.md` file in `docs/` or `eval/` folder

### Step 2: Buffy Verifies

I run backtests and analysis to verify:
- Does the edge actually exist?
- Are the claims supported by data?
- Are there any bugs or lookahead bias?
- Is it better than existing strategies?

**Output:** Verification report with PASS/FAIL status

### Step 3: Implementation (If PASS)

If verified, I implement:
- Update EA code
- Add to backtest suite
- Update documentation
- Deploy to paper trading

### Step 4: Monitoring (Ongoing)

Both agents monitor:
- Paper trading results
- Pattern decay
- Performance vs claims

---

## Verification Checklist

Before accepting any strategy, I check:

### Data Integrity
- [ ] No lookahead bias
- [ ] Uses actual tick data (not simulated)
- [ ] Covers multiple symbols
- [ ] Includes transaction costs

### Statistical Validity
- [ ] Sample size > 100 trades
- [ ] Win rate > 55%
- [ ] Profit factor > 1.0
- [ ] Max drawdown < 15%

### Implementation Quality
- [ ] No hardcoded future data
- [ ] Proper error handling
- [ ] Compatible with existing EA
- [ ] Logs all trades

### Edge Decay Check
- [ ] Pattern exists in Aug 2026 data
- [ ] Pattern exists in Jul 2026 data
- [ ] No obvious overfitting

---

## Document Status Tracking

| Document | Status | Last Verified | Next Check |
|----------|--------|---------------|------------|
| STRATEGY_V3.md | ✅ Verified | Sep 1, 2026 | Sep 15, 2026 |
| SPIKE_PROTECTION_PLAYBOOK.md | ⚠️ Partial | Sep 1, 2026 | Sep 8, 2026 |
| WHAT_ACTUALLY_WORKS.md | ✅ Verified | Sep 1, 2026 | Sep 15, 2026 |
| CLAIMS_VS_REALITY.md | ✅ Verified | Sep 1, 2026 | Sep 15, 2026 |

---

## Communication Protocol

### When Claude Code Proposes Something

1. Create the strategy document
2. Tag it with status: `PROPOSED`
3. List expected performance claims
4. Include implementation details

### When I Verify

1. Run backtests on the strategy
2. Document actual results vs claims
3. Tag with status: `VERIFIED` or `REJECTED`
4. If VERIFIED: implement in EA
5. If REJECTED: document why

### When Deploying

1. Update EA code
2. Update documentation
3. Tag with status: `DEPLOYED`
4. Add to monitoring schedule

---

## Example Workflow

### Claude Code says:
```
Proposing: "Adaptive Entry Delay Strategy"
Hypothesis: Entry at bar 3 (instead of 2) improves WR by 5%
Expected: WR 62-67% (vs current 57-63%)
```

### I verify:
```bash
python eval/backtest_entry_delay.py --delay 3
# Results: WR 59.2% (only +2% improvement)
# Verdict: MARGINAL - not worth the extra bar wait
# Status: REJECTED (insufficient improvement)
```

### Or I verify:
```bash
python eval/backtest_entry_delay.py --delay 3
# Results: WR 65.8% (8% improvement!)
# Verdict: SIGNIFICANT - implement
# Status: VERIFIED → IMPLEMENTED
```

---

## File Structure

```
docs/
├── STRATEGY_DEVELOPMENT_WORKFLOW.md  (this file)
├── STRATEGY_V3.md                   (verified, deployed)
├── CLAIMS_VS_REALITY.md             (verified, reference)
├── WHAT_ACTUALLY_WORKS.md           (verified, reference)
├── SPIKE_PROTECTION_PLAYBOOK.md     (partially verified)
├── DEPLOYMENT_GUIDE.md              (reference)
├── CHEAT_SHEET.md                   (reference)
└── FINAL_ASSESSMENT.md              (verified)

eval/
├── backtest_*.py                    (verification scripts)
├── paper_trading_tracker.py         (monitoring)
└── verify_*.py                      (validation scripts)

mt5/
└── SpikeCatcher_v2.mq5             (implemented, verified)
```

---

## Rules of Engagement

### Claude Code Must:
1. ✅ Provide clear hypothesis
2. ✅ Include expected performance
3. ✅ Cite data sources
4. ✅ Acknowledge limitations

### Buffy Must:
1. ✅ Run actual backtests
2. ✅ Compare claims vs reality
3. ✅ Flag any issues found
4. ✅ Only implement verified strategies
5. ✅ Update documentation

### Neither Should:
1. ❌ Skip verification
2. ❌ Trust claims without evidence
3. ❌ Deploy untested code
4. ❌ Ignore red flags

---

## Quality Gates

### Gate 1: Concept Validation
- Does the theory make sense?
- Is there a logical reason it should work?
- **Who checks:** Claude Code (concept) + Buffy (sanity check)

### Gate 2: Backtest Verification
- Does it work on historical data?
- Are the claims accurate?
- **Who checks:** Buffy (runs backtests)

### Gate 3: Forward Testing
- Does it work on unseen data?
- Do patterns persist?
- **Who checks:** Buffy (monitors paper trading)

### Gate 4: Live Deployment
- Is it safe to trade real money?
- What are the risks?
- **Who checks:** Both (joint decision)

---

## Success Metrics

### Strategy Quality
- Backtest WR: >55%
- Backtest PF: >1.0
- Forward test WR: >50%
- Live WR: >48% (after costs)

### Process Quality
- Verification time: <24 hours
- Implementation time: <48 hours
- Documentation: Always up to date
- Red flags: Never ignored

---

*Workflow established: September 1, 2026*
*Status: Active*
*Next review: September 15, 2026*

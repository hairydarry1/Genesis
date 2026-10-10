#!/usr/bin/env bash
# run_dsr_gate.sh — completes the CPCV+DSR gate automatically:
#   1. Waits until all four phase1 CPCV runs have written their final verdict
#      (run_cpcv overwrites the --output file with the JSON summary at the end).
#   2. Re-runs walk-forward policy validation per index with the CURRENT code,
#      which saves episode_returns needed for DSR skew/kurtosis estimation.
#   3. Runs Deflated Sharpe Ratio on each fresh policy_validation.json.
#
# Results land in dsr_gate_summary.txt. Kill with Ctrl-C if you change your mind.

set -u

N_TRIALS="${N_TRIALS:-16}"          # strategy variants tested across the project
MAX_WAIT_HOURS="${MAX_WAIT_HOURS:-14}"
POLL_SECONDS=300

CPCV_FILES=(phase1_cpcv_r75.txt phase1_cpcv_vol10.txt phase1_cpcv_boom.txt phase1_cpcv_crash.txt)

# name|dataset|world_model|report_output (empty wm = dataset's default)
RUNS=(
  "r75|data/dataset_R75_h10.npz||checkpoints_tiny/policy_validation.json"
  "vol10|data/dataset_volatility_10_h10.npz|checkpoints_vol10/world_model_best.pt|checkpoints_vol10/policy_validation.json"
  "boom500|data/dataset_boom_500_h10.npz|checkpoints_boom_500/world_model_best.pt|checkpoints_boom_500/policy_validation.json"
  "crash500|data/dataset_crash_500_h10.npz|checkpoints_crash_500/world_model_best.pt|checkpoints_crash_500/policy_validation.json"
)

echo "[gate] $(date) waiting for CPCV completion (max ${MAX_WAIT_HOURS}h)..."
deadline=$(( $(date +%s) + MAX_WAIT_HOURS * 3600 ))
while true; do
  done_count=0
  for f in "${CPCV_FILES[@]}"; do
    # run_cpcv finishes by dumping the JSON summary (contains "verdict") over the output file
    grep -q '"verdict"' "$f" 2>/dev/null && done_count=$((done_count + 1))
  done
  echo "[gate] $(date) ${done_count}/4 CPCV runs finished"
  [ "$done_count" -eq 4 ] && break
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "[gate] TIMEOUT waiting for CPCV after ${MAX_WAIT_HOURS}h — aborting." >&2
    exit 1
  fi
  sleep "$POLL_SECONDS"
done
echo "[gate] $(date) CPCV complete — starting policy validation re-runs"

summary="dsr_gate_summary.txt"
: > "$summary"
echo "# DSR gate results — generated $(date)" >> "$summary"
echo "# n_trials=${N_TRIALS}" >> "$summary"

overall_pass=0
for entry in "${RUNS[@]}"; do
  IFS='|' read -r name dataset wm report <<< "$entry"

  echo ""
  echo "[gate] === policy validation: $name ==="
  args=(--dataset "$dataset" --output "$report")
  [ -n "$wm" ] && args+=(--world-model "$wm")
  if ! python -m eval.policy_validation "${args[@]}" > "policy_validation_dsr_${name}.txt" 2>&1; then
    echo "[gate] policy validation FAILED for $name (see policy_validation_dsr_${name}.txt)" >&2
    echo "$name POLICY_VALIDATION_FAILED" >> "$summary"
    continue
  fi

  echo "[gate] --- DSR ($name, n_trials=$N_TRIALS) ---"
  python -m eval.dsr --policy-report "$report" --n-trials "$N_TRIALS" | tee "dsr_${name}.json"
  sig=$(python -c "
import json,sys
try:
    d = json.load(open('dsr_${name}.json'))
except Exception:
    sys.exit(1)
print(d.get('significant'))
" 2>/dev/null)
  echo "$name dsr_significant=${sig:-ERROR}" >> "$summary"
  [ "$sig" = "True" ] && overall_pass=$((overall_pass + 1))
done

echo "" | tee -a "$summary"
echo "GATE RESULT: DSR significant on ${overall_pass}/4 indices" | tee -a "$summary"
echo "[gate] $(date) done. Full details: $summary"
[ "$overall_pass" -eq 4 ]

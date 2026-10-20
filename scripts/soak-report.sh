#!/bin/sh
# Sums up the soak tests' logs (tests/soak.py into build/soak-*.log, scripts/win-test/soak.py into
# build/win-soak.log): rounds passed and failed, whether each is still running, and every failure in full.
cd "$(dirname "$0")/.." || exit 1
for log in build/soak-*.log build/win-soak.log; do
  [ -f "$log" ] || continue
  ok=$(grep -c ': ok ' "$log")
  failed=$(grep -c ': FAILED ' "$log")
  last=$(grep ' round ' "$log" | tail -n 1 | cut -c1-8)
  printf '%-22s %5s passed  %3s failed  last round %s\n' "$log" "$ok" "$failed" "${last:-none}"
done
if pgrep -f "soak.py" >/dev/null; then echo "Still running."; else echo "Finished."; fi
for log in build/soak-*.log build/win-soak.log; do
  [ -f "$log" ] && grep -q ': FAILED ' "$log" && { echo; echo "== $log"; grep -A 12 ': FAILED ' "$log"; }
done
exit 0

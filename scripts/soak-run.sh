#!/bin/sh
# Runs every Cherry Move soak test unattended for MINUTES (default 240), then says how it went.
#   scripts/soak-run.sh [MINUTES]
# Two Linux soaks (tests/soak.py: Cherry OS's own Cherry Move and the Linux build of the app), plus the Windows
# soak (scripts/win-test/soak.py) if the Windows test VM and winvm.py serve are up. Every round sends a random
# home folder, restores it and compares every file; a failed round keeps its folders and a FAILED.txt with
# every problem and the command to run it again. At the end: the Windows VM is shut down, the summary goes to
# build/soak-summary.txt and a desktop notification pops up.
cd "$(dirname "$0")/.." || exit 1
if [ -z "$SOAK_DETACHED" ]; then  # carry on even if the terminal that started it closes
  SOAK_DETACHED=1 setsid nohup scripts/soak-run.sh "$@" > build/soak-run.log 2>&1 < /dev/null &
  echo "Soak tests started; you'll get a notification when they finish."
  exit 0
fi
MINUTES=${1:-240}
export PYTHONPATH=desktop/lib
for s in a b; do
  build/venv/bin/python tests/soak.py --minutes "$MINUTES" --dir "build/soak-$s" > "build/soak-$s.log" 2>&1 &
done
win=
if timeout 120 python3 scripts/win-test/winvm.py run 'hostname' >/dev/null 2>&1; then
  build/venv/bin/python scripts/win-test/soak.py --minutes "$MINUTES" > build/win-soak.log 2>&1 &
  win=1
else
  rm -f build/win-soak.log
fi
wait
if [ -n "$win" ] && [ -S build/win10-soak/qmp.sock ]; then
  VM_DIR=build/win10-soak python3 scripts/qmp.py powerdown
fi
scripts/soak-report.sh > build/soak-summary.txt 2>&1
failed=$(grep -h ': FAILED ' build/soak-*.log build/win-soak.log 2>/dev/null | wc -l)
passed=$(grep -h ': ok ' build/soak-*.log build/win-soak.log 2>/dev/null | wc -l)
if [ "$failed" -eq 0 ]; then
  notify-send -a "Cherry soak tests" "Cherry soak tests finished" "All $passed rounds passed. Details: build/soak-summary.txt"
else
  notify-send -u critical -a "Cherry soak tests" "Cherry soak tests: $failed failed" \
    "$passed passed, $failed failed. Details: build/soak-summary.txt and FAILED.txt in each failed round's folder"
fi

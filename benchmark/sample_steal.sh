#!/usr/bin/env bash
# sample_steal.sh  — runs ON a cluster node (kube / sphere / cone).
# Reads /proc/stat every INTERVAL for DURATION seconds and emits busy% and
# steal% (CPU the vCPU was ready to use but the hypervisor withheld). This is the
# direct evidence for the oversubscription confound.
#
# Invoke from the orchestrating host:
#   ssh <node> "bash -s $DURATION $INTERVAL" < sample_steal.sh > steal_<node>.jsonl
#
# /proc/stat "cpu" fields: user(2) nice(3) system(4) idle(5) iowait(6) irq(7)
#                          softirq(8) steal(9) guest(10) guest_nice(11)
#
DURATION="${1:-60}"
INTERVAL="${2:-2}"

read_stat() {
  awk '/^cpu /{ idle = $5 + $6; steal = $9; total = 0; for (i = 2; i <= 11; i++) total += $i; print total, idle, steal }' /proc/stat
}

end=$(( $(date +%s) + DURATION ))
set -- $(read_stat); pt=$1; pi=$2; ps=$3
while [ "$(date +%s)" -lt "$end" ]; do
  sleep "$INTERVAL"
  set -- $(read_stat); ct=$1; ci=$2; cs=$3
  dt=$(( ct - pt )); di=$(( ci - pi )); ds=$(( cs - ps ))
  ts=$(date +%s)
  awk -v dt="$dt" -v di="$di" -v ds="$ds" -v ts="$ts" \
    'BEGIN { if (dt <= 0) dt = 1; printf "{\"epoch\":%d,\"busy_pct\":%.1f,\"steal_pct\":%.1f}\n", ts, (dt - di) / dt * 100, ds / dt * 100 }'
  pt=$ct; pi=$ci; ps=$cs
done

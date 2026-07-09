#!/usr/bin/env bash
# sample_backend.sh  — runs ON the control-plane node (kube).
# Samples CRDP pod CPU (per-node sum + total) and node CPU every INTERVAL for
# DURATION seconds. Emits one JSON object per sample to stdout (JSONL), each
# timestamped with epoch so the aggregator can select the steady window.
#
# Invoke from the orchestrating host:
#   ssh kube "bash -s $DURATION $INTERVAL" < sample_backend.sh > backend.jsonl
#
DURATION="${1:-60}"
INTERVAL="${2:-2}"
K="/var/lib/rancher/rke2/bin/kubectl --kubeconfig=$HOME/.kube/config"

# Pod -> node map (stable during a run; pods do not move mid-run).
MAP="/tmp/crdp_podmap.$$"
$K get pods -l run=crdp -o wide --no-headers 2>/dev/null | awk '{print $1, $7}' > "$MAP"

end=$(( $(date +%s) + DURATION ))
while [ "$(date +%s)" -lt "$end" ]; do
  ts=$(date +%s)
  pod=$($K top pods -l run=crdp --no-headers 2>/dev/null | awk -v MAP="$MAP" '
    BEGIN { while ((getline l < MAP) > 0) { split(l, a, " "); node[a[1]] = a[2] } }
    { gsub(/m/, "", $2); n = node[$1]; sum[n] += $2; tot += $2 }
    END {
      out = ""
      for (k in sum) out = out (out ? "," : "") "\"" k "\":" sum[k]
      printf "{\"total_m\":%d,\"per_node_m\":{%s}}", tot + 0, out
    }')
  nodes=$($K top nodes --no-headers 2>/dev/null | awk '{printf "%s\"%s\":\"%s\"", (NR > 1 ? "," : ""), $1, $3}')
  printf '{"epoch":%s,"pod":%s,"node_cpu_pct":{%s}}\n' "$ts" "${pod:-{\"total_m\":0,\"per_node_m\":{}}}" "$nodes"
  sleep "$INTERVAL"
done
rm -f "$MAP"

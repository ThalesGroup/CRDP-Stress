#!/usr/bin/env bash
# sample_backend.sh  — runs ON the control-plane node (kube).
# Samples CRDP pod CPU (per-node sum + total) every INTERVAL for DURATION seconds.
# Emits one JSON object per sample to stdout (JSONL), timestamped with epoch so the
# aggregator can select the steady window. (Node CPU% is captured separately and
# more accurately via /proc/stat in sample_steal.sh.)
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
  # A single awk emits the whole JSON line to avoid brace-nesting mistakes.
  $K top pods -l run=crdp --no-headers 2>/dev/null | awk -v MAP="$MAP" -v ts="$ts" '
    BEGIN { while ((getline l < MAP) > 0) { split(l, a, " "); node[a[1]] = a[2] } }
    { gsub(/m/, "", $2); n = node[$1]; sum[n] += $2; tot += $2 }
    END {
      pn = ""
      for (k in sum) pn = pn (pn ? "," : "") "\"" k "\":" sum[k]
      printf "{\"epoch\":%s,\"total_m\":%d,\"per_node_m\":{%s}}\n", ts, tot + 0, pn
    }'
  sleep "$INTERVAL"
done
rm -f "$MAP"

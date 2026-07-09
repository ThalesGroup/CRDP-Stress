#!/usr/bin/env bash
# Expose RKE2 control-plane metrics so an external Prometheus can scrape them.
# RKE2 binds etcd (:2381), kube-scheduler (:10259), kube-controller-manager
# (:10257) and kube-proxy (:10249) to 127.0.0.1 by default; this rewrites their
# bind addresses. It also sets the kubelet's cAdvisor housekeeping interval to 5s.
#
#   sudo ./apply_rke2_metrics.sh server    # on the RKE2 server (control-plane) node
#   sudo ./apply_rke2_metrics.sh agent     # on each RKE2 agent (worker) node
#   sudo ./apply_rke2_metrics.sh rollback  # restore the newest backup
#
# Idempotent: the added keys live in a delimited block that is stripped and
# rewritten on every run, so re-running never duplicates YAML keys. Everything
# outside the block -- on agents, `server:` and the cluster join `token:` -- is
# left untouched.
#
# Restarting rke2 bounces kubelet and containerd, but containerd shims keep
# running containers alive across the restart, so CRDP pods should show zero
# added restarts. Verify that afterwards. Do not run during a benchmark.
set -euo pipefail

CFG=/etc/rancher/rke2/config.yaml
BEGIN="# >>> crdp-monitoring managed block >>>"
END="# <<< crdp-monitoring managed block <<<"

ROLE="${1:-}"
case "$ROLE" in
  server)   UNIT=rke2-server ;;
  agent)    UNIT=rke2-agent ;;
  rollback) ;;
  *) echo "usage: $0 server|agent|rollback" >&2; exit 2 ;;
esac

[ "$(id -u)" -eq 0 ] || { echo "must run as root (use sudo)" >&2; exit 1; }

if [ "$ROLE" = rollback ]; then
  newest=$(ls -1t "${CFG}".bak.* 2>/dev/null | head -1 || true)
  [ -n "$newest" ] || { echo "no backup found matching ${CFG}.bak.*" >&2; exit 1; }
  cp -a "$newest" "$CFG"
  echo "restored $newest -> $CFG"
  systemctl restart rke2-server 2>/dev/null || systemctl restart rke2-agent
  echo "restarted rke2. done."
  exit 0
fi

mkdir -p "$(dirname "$CFG")"
[ -f "$CFG" ] || : > "$CFG"

# Refuse to run if these keys already exist OUTSIDE our managed block: appending
# a second copy would make the YAML invalid and RKE2 would fail to start.
stripped=$(awk -v b="$BEGIN" -v e="$END" '$0==b{s=1} !s{print} $0==e{s=0}' "$CFG")
for key in etcd-expose-metrics kube-controller-manager-arg kube-scheduler-arg kube-proxy-arg kubelet-arg; do
  # Here-string, not `printf | grep -q`: under `set -o pipefail` a matching
  # `grep -q` can kill the writer with SIGPIPE and fail the whole pipeline.
  if grep -qE "^${key}:" <<<"$stripped"; then
    echo "ERROR: '$key' is already set in $CFG outside the managed block." >&2
    echo "Merge it by hand; refusing to create a duplicate YAML key." >&2
    exit 1
  fi
done

if [ -s "$CFG" ]; then
  backup="${CFG}.bak.$(date +%Y%m%d-%H%M%S)"
  cp -a "$CFG" "$backup"
  echo "backed up $CFG -> $backup"
fi

{
  if [ -n "$stripped" ]; then printf '%s\n' "$stripped"; fi
  printf '%s\n' "$BEGIN"
  printf 'kube-proxy-arg:\n  - "metrics-bind-address=0.0.0.0:10249"\n'
  # cAdvisor stamps its OWN timestamp on every sample, and Prometheus honors it.
  # So the effective resolution of container_cpu_usage_seconds_total is the
  # kubelet's cAdvisor housekeeping interval (default 10s), NOT the scrape
  # interval -- scraping faster merely re-ingests an identical (ts, value) pair.
  # At the default, rate(...[15s]) over a 20s benchmark phase returns nothing for
  # most pods, because a pod needs two DISTINCT samples inside the window.
  # 5s matches the scrape interval and costs a negligible amount of CPU.
  printf 'kubelet-arg:\n  - "housekeeping-interval=5s"\n'
  if [ "$ROLE" = server ]; then
    printf 'etcd-expose-metrics: true\n'
    printf 'kube-controller-manager-arg:\n  - "bind-address=0.0.0.0"\n'
    printf 'kube-scheduler-arg:\n  - "bind-address=0.0.0.0"\n'
  fi
  printf '%s\n' "$END"
} > "${CFG}.new"
mv "${CFG}.new" "$CFG"
chmod 0600 "$CFG"
echo "--- new $CFG ---"; cat "$CFG"; echo "----------------"

echo "restarting $UNIT ..."
systemctl restart "$UNIT"

# Wait for the node to come back. On the server, wait for the apiserver to
# answer; on an agent, waiting for the unit plus the kubelet port is enough.
KUBECTL="/var/lib/rancher/rke2/bin/kubectl"
for i in $(seq 1 60); do
  if [ "$ROLE" = server ]; then
    if "$KUBECTL" --kubeconfig /etc/rancher/rke2/rke2.yaml get --raw /readyz >/dev/null 2>&1; then
      echo "apiserver ready after ${i}0s"; break
    fi
  else
    # No -q: it would exit early and SIGPIPE `ss` under pipefail.
    if ss -lnt | grep ':10250 ' >/dev/null; then echo "kubelet listening after ${i}0s"; break; fi
  fi
  if [ "$i" -eq 60 ]; then
    echo "TIMED OUT waiting for $UNIT to become ready" >&2
    exit 1
  fi
  sleep 10
done

echo "--- metrics listeners (want 0.0.0.0/*, not 127.0.0.1) ---"
ss -lntp 2>/dev/null | grep -E ':(2381|10249|10257|10259) ' || echo "(none listening yet; give rke2 ~30s and re-check)"

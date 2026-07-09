#!/usr/bin/env bash
# Install node_exporter on a Debian/Ubuntu host that is NOT a Kubernetes node --
# typically the host that runs the CRDP stress client.
#
# The DaemonSet in ../k8s/ covers every cluster node. It does not cover the load
# client, which needs monitoring for two reasons:
#
#   * A throughput result only means something if the BACKEND was the bottleneck.
#     If the load client's CPU is pegged, you measured your load generator.
#   * node_cpu_seconds_total{mode="steal"} shows hypervisor contention. If the
#     load client shares a hypervisor with a cluster node, generating load steals
#     cycles from that node's own CRDP pods.
#
# Installs the distro package (systemd) rather than the Docker image, so the
# client's container runtime stays idle during a run.
#
# Requires passwordless sudo. Safe to re-run.
#
#   ssh USER@LOAD_CLIENT_IP 'bash -s' < install_node_exporter.sh
set -euo pipefail

echo "==> installing prometheus-node-exporter"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq prometheus-node-exporter

echo "==> enabling + starting"
sudo systemctl enable --now prometheus-node-exporter

echo "==> waiting for :9100"
for i in $(seq 1 15); do
  if ss -lnt | grep -q ':9100 '; then break; fi
  if [ "$i" -eq 15 ]; then
    echo "ERROR: nothing listening on :9100 after 15s" >&2
    sudo systemctl status prometheus-node-exporter --no-pager || true
    exit 1
  fi
  sleep 1
done

echo "==> verifying the metrics we actually care about"
metrics=$(curl -fsS http://localhost:9100/metrics)
for m in node_cpu_seconds_total node_memory_MemAvailable_bytes; do
  n=$(printf '%s\n' "$metrics" | grep -c "^${m}" || true)
  echo "    ${m}: ${n} series"
  [ "$n" -gt 0 ] || { echo "ERROR: $m missing" >&2; exit 1; }
done

# The steal series is the whole reason this host is monitored; assert it exists.
#
# Matched with `case`, not `printf ... | grep -q`. Under `set -o pipefail` that
# pipeline FAILS WHEN THE PATTERN MATCHES: grep -q exits on the first hit, printf
# is still writing ~100KB of metrics, takes SIGPIPE (141), and pipefail surfaces
# that as the pipeline's status. `case` needs no pipe and no subprocess.
case "$metrics" in
  *'node_cpu_seconds_total{cpu="0",mode="steal"}'*)
    echo '    mode="steal" series: present' ;;
  *)
    echo 'ERROR: no mode="steal" series -- is this a VM?' >&2
    exit 1 ;;
esac

echo "==> OK. node_exporter is live on $(hostname -I | awk '{print $1}'):9100"
echo "    ufw status: $(sudo ufw status 2>/dev/null | head -1 || echo 'ufw not installed')"

#!/usr/bin/env bash
# Install node_exporter on the load-generation client cm-neptune (192.168.1.193).
#
# cm-neptune is NOT a Kubernetes node, so the DaemonSet in ../k8s/ does not cover
# it. We install the distro package (systemd) rather than the Docker image so the
# host's container runtime stays idle during benchmark runs -- cm-neptune drives
# the load, and we do not want a container competing for its 16 vCPUs.
#
# Why the load client needs monitoring at all: the throughput report has to prove
# runs are backend-bound, not client-bound. node_exporter gives host CPU and
# node_cpu_seconds_total{mode="steal"} -- cm-neptune shares hypervisor "Lemonade"
# with cluster node `cone`, so its steal directly biases cone's pods.
#
#   ssh rrobinson@cm-neptune.test256.io 'bash -s' < install_cm_neptune.sh
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

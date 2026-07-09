# Prometheus monitoring for the CRDP cluster and load client

## The question this answers first: push or pull?

**Prometheus pulls. The nodes never send anything.**

`prometheus.test256.io` (192.168.1.186) reaches out on a timer and performs an HTTP `GET /metrics`
against each target. There is nothing to configure on a node to "send its data" — you only have to
(a) make something on that node *serve* `/metrics`, and (b) make sure Prometheus can reach it.

Push does exist in Prometheus, but only in two narrow forms, and neither applies here:

| Mechanism | What it's for | Why not here |
| --- | --- | --- |
| **Pushgateway** | short-lived batch jobs that exit before any scrape can catch them | our targets are long-running daemons |
| **`remote_write`** | one Prometheus forwarding samples to another, or to long-term storage | we have exactly one Prometheus |

So the entire job reduces to: **stand up exporters, then add scrape jobs to the existing server.**

## Architecture

The user chose a **central-scrape** design: `prometheus.test256.io` stays the one and only Prometheus.
We deliberately did **not** install Rancher's `rancher-monitoring` chart (RKE2 = *Rancher* Kubernetes
Engine 2; the chart repackages upstream `kube-prometheus-stack`). It would have placed a second
Prometheus, a Grafana and an Alertmanager *inside* the cluster — burning CPU on the exact three nodes
whose CPU this project exists to measure. Only lightweight exporters run in-cluster.

```
                    prometheus.test256.io (192.168.1.186)   Docker, Prometheus 2.53.1
                    grafana                (192.168.1.186:3000)
                                   │  pulls every 5-15s
   ┌──────────────┬────────────────┼─────────────────┬──────────────┬──────────────┐
   ▼              ▼                ▼                 ▼              ▼              ▼
 node_exporter  cAdvisor +       control plane   kube-state-    cm-kirk :443   node_exporter
 :9100 x3       kubelet :10250   etcd     :2381  metrics        key manager    :9100
 DaemonSet      (Bearer token)   sched    :10259 NodePort                      systemd/apt
 hostNetwork     ↑               ctrl-mgr :10257 :30080                        cm-neptune
 kube/sphere/    per-pod CPU     kube-proxy:10249                              (load client,
  cone                           apiserver :6443                                not a k8s node)
```

| Host | IP | Role |
| --- | --- | --- |
| prometheus | 192.168.1.186 | Prometheus + Grafana (Docker) |
| sphere | 192.168.1.187 | RKE2 worker |
| kube | 192.168.1.188 | RKE2 server (control plane + etcd) |
| cone | 192.168.1.189 | RKE2 worker |
| cm-kirk | 192.168.1.190 | CipherTrust Manager (key manager) |
| cm-neptune | 192.168.1.193 | load generator — **not** a cluster node |

## Two facts about this deployment that shaped the design

**CRDP exposes no `/metrics`.** Its container declares only port 8090; `/metrics`, `/v1/metrics` and
`/actuator/prometheus` all return the application's own JSON 404, and it defines no liveness or
readiness probes. Per-pod CPU therefore comes from **cAdvisor** (via the kubelet), not from the app.
That is fine, and in fact better than what we had.

**cAdvisor fixes a real bug — but only once it is tuned.** `container_cpu_usage_seconds_total` is a
counter, so `rate()` over it is exact. metrics-server (`kubectl top`) lags **15–25 seconds**, which
silently corrupted the first benchmark aggregation — a 39-core CPU peak landed *after* the load had
stopped, producing `cores=1.0` for the PROTECT phase and nonsense efficiency.
`benchmark/sample_backend.sh` exists purely as a workaround for that lag.

The catch: **cAdvisor stamps its own timestamp on every sample, and Prometheus honors it.** Scraping
every 5 s therefore re-ingests an identical `(timestamp, value)` pair, and the *effective* resolution is
the kubelet's cAdvisor housekeeping interval — **10 s by default** (measured here: the counter advanced
once per ~12.6 s). At that cadence `rate(...[15s])` over a 22-second PROTECT phase returned data for
only **2 of 24 pods**, because a pod needs two *distinct* samples inside the window.

Both ends are fixed: `kubelet-arg: housekeeping-interval=5s` (`rke2/`) makes the samples genuinely
fresh, and `honor_timestamps: false` on the cadvisor job makes Prometheus stamp them at scrape time.
Use a `rate()` window of **at least 30 s**.

## Layout

```
Monitoring/
  k8s/          namespace, node-exporter DaemonSet, Prometheus RBAC + token, kube-state-metrics
  rke2/         config snippets + apply_rke2_metrics.sh (moves control-plane metrics off 127.0.0.1)
  node_exporter/install_cm_neptune.sh   (the load client is not in the cluster)
  prometheus/   scrape_configs.yml template + CM_KIRK_TOKEN.md (fixes the existing 401)
  grafana/      DASHBOARDS.md (Grafana already runs on the Prometheus host)
  promql/       benchmark_queries.md
benchmark/prom_snapshot.py              rebuilds the aggregator's JSONL from Prometheus
```

## Deploy

Run from the repo root. `kubectl` on RKE2 is not on `PATH`; use the absolute path shown.

```bash
KUBECTL="/var/lib/rancher/rke2/bin/kubectl --kubeconfig=$HOME/.kube/config"
SSH="ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes"
```

**0. Control-plane metrics (restarts RKE2 — never during a benchmark).**
RKE2 binds etcd, scheduler, controller-manager and kube-proxy metrics to `127.0.0.1`. This moves them
onto all interfaces, one node at a time, backing up each `config.yaml` first. On `sphere`/`cone` that
file also holds the cluster join token, so the script **merges** rather than overwrites.

```bash
$SSH rrobinson@192.168.1.188 'sudo bash -s server' < Monitoring/rke2/apply_rke2_metrics.sh
$SSH rrobinson@192.168.1.187 'sudo bash -s agent'  < Monitoring/rke2/apply_rke2_metrics.sh
$SSH rrobinson@192.168.1.189 'sudo bash -s agent'  < Monitoring/rke2/apply_rke2_metrics.sh
# rollback:  sudo ./apply_rke2_metrics.sh rollback
```

**1. In-cluster exporters** (no sudo needed):

```bash
$KUBECTL apply -f Monitoring/k8s/
```

**2. Load client:**

```bash
$SSH rrobinson@192.168.1.193 'bash -s' < Monitoring/node_exporter/install_cm_neptune.sh
```

**3. Prometheus host.** Render the scrape config with the ServiceAccount token, append it under the
existing `scrape_configs:` key, and restart:

```bash
TOKEN=$($SSH rrobinson@192.168.1.188 \
  "$KUBECTL -n monitoring get secret prometheus-token -o jsonpath='{.data.token}'" | base64 -d)
sed "s|__K8S_TOKEN__|$TOKEN|g" Monitoring/prometheus/scrape_configs.yml > /tmp/jobs.yml
# append to /etc/prometheus/prometheus.yml on .186, then:
$SSH rrobinson@192.168.1.186 \
  'sudo docker restart "$(sudo docker ps -q --filter ancestor=prom/prometheus)"'
```

Two constraints on that host, both discovered rather than assumed:

- Only the **file** `/etc/prometheus/prometheus.yml` is bind-mounted into the container, not its
  directory. A sibling token file would be invisible inside the container, so the token is **inlined**
  via `authorization.credentials` — matching what the pre-existing CM-Kirk job already does with
  `bearer_token`.
- The container runs **without `--web.enable-lifecycle`**, so `POST /-/reload` returns 405. Config
  changes need `docker restart`. The TSDB is on a named volume; no samples are lost.

**4. Fix the CipherTrust Manager job.** It had been DOWN with HTTP 401 since before this work; a fresh
metrics token brought it UP. See [`prometheus/CM_KIRK_TOKEN.md`](prometheus/CM_KIRK_TOKEN.md) — the
metrics token does *not* expire, so a 401 means it was invalidated (most likely when the environment was
last redeployed), not that it aged out.

**5. Grafana** already runs on `192.168.1.186:3000`. Add the datasource and dashboards per
[`grafana/DASHBOARDS.md`](grafana/DASHBOARDS.md).

## Current state (verified)

**11 jobs, 20 targets, all UP.**

| Job | Targets | Notes |
| --- | --- | --- |
| `node-exporter` | 4 | kube, sphere, cone, **cm-neptune** |
| `cadvisor` | 3 | per-pod CRDP CPU; 5 s housekeeping |
| `kubelet` | 3 | |
| `kube-proxy` | 3 | needed the RKE2 change |
| `etcd`, `kube-scheduler`, `kube-controller-manager`, `kube-apiserver` | 1 each | needed the RKE2 change |
| `kube-state-metrics` | 1 | NodePort 30080 |
| `CM-Kirk` | 1 | key manager; token refreshed |
| `prometheus` | 1 | self-scrape |

The powered-off nodes `cylinder` and `torus` were deleted from the cluster during this work; they had
been holding stale `Running` DaemonSet pods (csi, canal, ingress-nginx, kube-proxy) that only *looked*
healthy because their kubelets were unreachable.

Three RKE2 restarts were performed, rolling, one node at a time. **CRDP stayed at 24/24 Running with
zero added restarts** throughout — containerd shims keep containers alive across a kubelet/containerd
bounce.

## Verify

```bash
# exporters answer, and nothing firewalls 9100/30080
for ip in 192.168.1.187 192.168.1.188 192.168.1.189 192.168.1.193; do
  echo -n "$ip:9100 -> "; curl -s "http://$ip:9100/metrics" | grep -c '^node_cpu_seconds_total'
done
curl -s http://192.168.1.188:30080/metrics | grep -c '^kube_pod_info'

# the token authorizes cAdvisor
curl -sk -H "Authorization: Bearer $TOKEN" \
     https://192.168.1.188:10250/metrics/cadvisor | head -3

# every target healthy
curl -s http://192.168.1.186:9090/api/v1/targets \
  | python3 -c 'import sys,json;[print(f"{t[\"labels\"][\"job\"]:24} {t[\"health\"]:8} {t.get(\"lastError\",\"\")}") for t in json.load(sys.stdin)["data"]["activeTargets"]]'
```

**Acceptance test — done, and it validates the published report.** An 8-client `digits` run from
cm-neptune, with the phase windows taken from the client JSONs:

```bash
py benchmark/prom_snapshot.py --start 1783622612 --end 1783622653 \
    --rate-window 30s --tps 512615 --out /tmp/promcheck
```

| PROTECT, backend cores | value |
| --- | --- |
| cAdvisor (direct cgroup measurement) | **44.3** |
| `/proc/stat` estimate — the method in `aggregate_profile.py` | **42.4** |
| `results/report.md` | **43.5** |

Two independent instruments agree within ~4 %, and both bracket the published figure. On REVEAL they
agree to **1.00** (36.7 vs 36.6). The report's core-attribution method is sound.

Neither strictly bounds the other, so do not expect one to always read lower: the `/proc` estimate
sweeps in kubelet, containerd and canal CPU *and* counts **steal as busy** (which inflates it), while
cAdvisor sees only CRDP's cgroups. `prom_snapshot.py` prints the ratio and flags anything outside
±10 % as `DISAGREE -- investigate`.

That same run also caught `cone` at **7.26 % steal** during REVEAL while `kube` and `sphere` sat near
zero — the cm-neptune↔`cone` hypervisor contention described in the report, now measured rather than
inferred.

## Security notes

- The ServiceAccount token and the CipherTrust Manager metrics token are **credentials**. `.gitignore`
  covers `*.token` and `k8s-token`; this repo holds only templates and procedures, never values.
- `etcd-expose-metrics: true` serves etcd metrics **unauthenticated** on `:2381` across
  `192.168.1.0/24`. It reveals cluster topology and key counts, never key *values*. Likewise
  node_exporter `:9100`, kube-proxy `:10249` and kube-state-metrics `:30080` are unauthenticated.
  Acceptable on a lab LAN. The hardening step is a `ufw` rule restricting those ports to 192.168.1.186.
- `insecure_skip_verify: true` on the kubelet, scheduler, controller-manager and apiserver jobs: RKE2's
  serving certificates are self-signed per node. The alternative is shipping the RKE2 CA into the
  Prometheus container, which the single-file bind mount currently prevents.

## Cost

node_exporter is capped at 20m CPU / 64Mi per node and kube-state-metrics at 100m / 192Mi, with
`requests` = `limits` so neither can burst into a benchmark run. Against three 16-vCPU nodes that is
roughly **0.1% of cluster CPU** — real, but measured rather than hidden.

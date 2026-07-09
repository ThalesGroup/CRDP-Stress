# Prometheus & Grafana monitoring for CRDP on Kubernetes

Observability for a CipherTrust RESTful Data Protection (CRDP) deployment: per-pod CPU and memory, node
saturation, CPU steal, Kubernetes object state, and RKE2 control-plane health — plus a ready-made Grafana
dashboard.

This is what turns a throughput number into an explanation. Without it you can measure that CRDP does
*N* transactions per second; with it you can say **why**, and what to add to go faster.

---

## Before you start: Prometheus pulls, it does not receive

**Prometheus reaches out and scrapes. Your nodes never send anything.**

On a schedule, the Prometheus server performs an HTTP `GET /metrics` against each configured target.
There is nothing to install on a node to make it "send its data". You only have to:

1. make something on that node **serve** `/metrics`, and
2. make sure the Prometheus server can **reach** it.

Push does exist in Prometheus, but in two narrow forms, and neither applies to a deployment like this:

| Mechanism | What it is for | Why it does not apply |
| --- | --- | --- |
| **Pushgateway** | short-lived batch jobs that exit before any scrape can catch them | these targets are long-running daemons |
| **`remote_write`** | one Prometheus forwarding samples to another, or to long-term storage | a single Prometheus server is sufficient |

So the whole task is: **install exporters, then add scrape jobs.** Everything below follows from that.

> In Grafana the word *data source* means something different — it is the Prometheus **server** that
> Grafana queries. You add nodes to *Prometheus* as **scrape targets**, and you add Prometheus to
> *Grafana* as a **data source**. Both are covered below.

---

## What is not enabled by default

Three things must be turned on explicitly. This is the heart of the installation.

**1. CRDP itself exposes no `/metrics` endpoint.** The CRDP container serves only its data-plane port
(`8090`); `/metrics`, `/v1/metrics` and `/actuator/prometheus` all return the application's own JSON
`404`, and it declares no liveness or readiness probes. There is no Prometheus support to switch on, and
no CRDP scrape job to configure.

That is not a problem. CRDP runs in containers, so **cAdvisor** — built into every kubelet — already
reports per-pod CPU and memory. That is where all CRDP resource data in this stack comes from.

**2. The kubelet and cAdvisor require authentication.** They listen on `:10250` on all interfaces but
answer `401` without a bearer token. `Monitoring/k8s/20-prometheus-rbac.yaml` creates a ServiceAccount
with exactly the rights needed and a long-lived token to present.

**3. RKE2 binds control-plane metrics to `127.0.0.1`.** etcd (`:2381`), kube-scheduler (`:10259`),
kube-controller-manager (`:10257`) and kube-proxy (`:10249`) are unreachable from another host until you
change their bind addresses. `Monitoring/rke2/apply_rke2_metrics.sh` does that.

And one setting that is enabled by default but **wrong for benchmarking**: the kubelet's cAdvisor
*housekeeping interval* is `10s`. cAdvisor stamps each sample with its own collection time and Prometheus
honors it, so scraping faster merely re-ingests an identical `(timestamp, value)` pair — the effective
resolution is the housekeeping interval, not the scrape interval. At `10s`, a `rate(...[15s])` query over
a 22-second load phase returns data for only a handful of pods, because a pod needs two *distinct*
samples inside the window. The install sets `housekeeping-interval=5s` and `honor_timestamps: false`.
**Always use a `rate()` window of at least 30 s.**

---

## Architecture

A **central-scrape** design: one Prometheus server, outside the cluster, scrapes everything. Only
lightweight exporters run in-cluster.

RKE2 ships a `rancher-monitoring` chart (a repackaging of upstream `kube-prometheus-stack`) that would
install Prometheus, Grafana and Alertmanager *inside* the cluster. That is deliberately **not** used
here: it consumes CPU on the very nodes whose CPU you are trying to measure. If you are not running
benchmarks and want a batteries-included stack, `rancher-monitoring` is a reasonable alternative.

```
                     Prometheus server  :9090     ── Grafana :3000
                                │  scrapes every 5-15s
   ┌──────────────┬─────────────┼──────────────┬──────────────┬──────────────┐
   ▼              ▼             ▼              ▼              ▼              ▼
 node_exporter  cAdvisor +   control plane  kube-state-   CipherTrust    node_exporter
 :9100          kubelet      etcd    :2381  metrics       Manager        :9100
 DaemonSet      :10250       sched   :10259 NodePort      :443           systemd pkg
 hostNetwork    (token)      ctrl-mgr:10257 :30080        (optional)     load client
 every node      ↑           kube-proxy:10249                            (not a
                 per-pod CPU apiserver :6443                              cluster node)
```

Fill in your own environment; every command below refers to these names:

| Placeholder | Meaning |
| --- | --- |
| `CONTROL_PLANE_IP` | RKE2 server node (control plane + etcd) |
| `WORKER_1_IP`, `WORKER_2_IP` | RKE2 agent nodes |
| `LOAD_CLIENT_IP` | host running the stress client — **not** a cluster node |
| `PROM_HOST` | host running Prometheus (and usually Grafana) |
| `SSH_USER` | a user with `sudo` on the cluster nodes |

The load client is monitored too, and this matters: a throughput result is only meaningful if you can
show the *backend* was the bottleneck. If the load client's CPU is pegged, you measured your load
generator, not CRDP.

---

## Layout

```
Monitoring/
  k8s/            namespace, node-exporter DaemonSet, Prometheus RBAC + token, kube-state-metrics
  rke2/           config snippets + apply_rke2_metrics.sh (exposes control-plane metrics)
  node_exporter/  install_node_exporter.sh   (for hosts outside the cluster)
  prometheus/     scrape_configs.yml template + CIPHERTRUST_MANAGER_METRICS.md
  grafana/        crdp-kubernetes-dashboard.json + DASHBOARDS.md
  promql/         benchmark_queries.md
benchmark/prom_snapshot.py   rebuilds the benchmark aggregator's JSONL from Prometheus
```

---

## Installation

### Prerequisites

- An RKE2 cluster, `kubectl` access, and `sudo` on each node.
- A host to run Prometheus and Grafana. **Step 0 installs them if you do not have them.**
- Network reachability from the Prometheus host to every node on ports
  `9100`, `10249`, `10250`, `2381`, `10257`, `10259`, `6443`, and `30080`.

On RKE2, `kubectl` is not on `PATH` by default:

```bash
KUBECTL="/var/lib/rancher/rke2/bin/kubectl --kubeconfig=/etc/rancher/rke2/rke2.yaml"
```

### Step 0 — Install Prometheus and Grafana (skip if you already have them)

Neither is included with RKE2 or CRDP. The quickest path is Docker on a host *outside* the cluster:

```bash
# Minimal starting config. The scrape jobs from Step 4 get appended to this file.
sudo mkdir -p /etc/prometheus
sudo tee /etc/prometheus/prometheus.yml >/dev/null <<'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
EOF

docker run -d --name prometheus --restart unless-stopped \
  -p 9090:9090 \
  -v /etc/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml \
  -v prometheus-data:/prometheus \
  prom/prometheus \
  --config.file=/etc/prometheus/prometheus.yml \
  --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=30d \
  --web.enable-lifecycle

docker run -d --name grafana --restart unless-stopped \
  -p 3000:3000 -v grafana-storage:/var/lib/grafana \
  grafana/grafana
```

Two flags worth understanding:

- **`--web.enable-lifecycle`** lets you apply config changes with
  `curl -X POST http://localhost:9090/-/reload` instead of restarting the container. Without it that
  endpoint returns `405` and you must `docker restart prometheus`. Restarting loses no data — the TSDB
  lives in the `prometheus-data` volume.
- **Bind-mount the directory, not the file**, if you want to use `credentials_file` for the bearer
  token instead of inlining it. Mounting only `prometheus.yml` (as above) makes any sibling token file
  invisible inside the container, which is why `scrape_configs.yml` inlines the token by default.

Check it: `curl http://PROM_HOST:9090/-/healthy` and open `http://PROM_HOST:3000` (default login
`admin` / `admin`; change it immediately).

### Step 1 — Expose the RKE2 control-plane metrics

**This restarts RKE2. Do not run it during a benchmark.** The script backs up each `config.yaml` first,
restarts one node at a time, and waits for readiness between nodes. On agent nodes the config file also
holds the cluster join token, so it **merges** rather than overwrites.

Containerd shims keep running containers alive across a kubelet/containerd restart, so CRDP pods keep
serving and their restart counters stay at zero. Verify that afterwards rather than assuming it.

```bash
# From the repo root, on a machine with SSH access to the nodes.
ssh SSH_USER@CONTROL_PLANE_IP 'sudo bash -s server' < Monitoring/rke2/apply_rke2_metrics.sh
ssh SSH_USER@WORKER_1_IP      'sudo bash -s agent'  < Monitoring/rke2/apply_rke2_metrics.sh
ssh SSH_USER@WORKER_2_IP      'sudo bash -s agent'  < Monitoring/rke2/apply_rke2_metrics.sh
```

This also sets `housekeeping-interval=5s` on every kubelet. To undo everything:

```bash
ssh SSH_USER@NODE_IP 'sudo bash -s rollback' < Monitoring/rke2/apply_rke2_metrics.sh
```

Confirm the listeners moved off loopback — they should show `0.0.0.0` or `*`, not `127.0.0.1`:

```bash
ssh SSH_USER@CONTROL_PLANE_IP "sudo ss -lntp | grep -E ':(2381|10249|10257|10259) '"
```

### Step 2 — Deploy the in-cluster exporters

No `sudo` required; this is plain `kubectl`.

```bash
$KUBECTL apply -f Monitoring/k8s/
$KUBECTL -n monitoring rollout status ds/node-exporter
$KUBECTL -n monitoring rollout status deploy/kube-state-metrics
```

This creates the `monitoring` namespace, a `node-exporter` DaemonSet publishing `:9100` on every node's
own IP (`hostNetwork`), `kube-state-metrics` on NodePort `30080`, and the `prometheus` ServiceAccount
with its token.

`requests` equal `limits` on both exporters (20m CPU / 64Mi for node-exporter; 100m / 192Mi for
kube-state-metrics) so neither can burst and perturb a benchmark run.

### Step 3 — Install node_exporter on the load client

The load-generating host is not a cluster node, so the DaemonSet does not cover it. Install the distro
package instead of a container, to keep the client's container runtime idle during runs:

```bash
ssh SSH_USER@LOAD_CLIENT_IP 'bash -s' < Monitoring/node_exporter/install_node_exporter.sh
```

The script is safe to re-run, and verifies that `node_cpu_seconds_total` and the `mode="steal"` series
are being published before it exits.

### Step 4 — Add the nodes to Prometheus as scrape targets

Retrieve the ServiceAccount token created in Step 2:

```bash
TOKEN=$($KUBECTL -n monitoring get secret prometheus-token -o jsonpath='{.data.token}' | base64 -d)
```

Render [`prometheus/scrape_configs.yml`](prometheus/scrape_configs.yml), substituting your addresses and
the token. Add one entry per node to the `node-exporter`, `cadvisor`, `kubelet` and `kube-proxy` jobs:

```bash
sed -e "s|__CONTROL_PLANE_IP__|10.0.0.11|g" \
    -e "s|__WORKER_1_IP__|10.0.0.12|g" \
    -e "s|__WORKER_2_IP__|10.0.0.13|g" \
    -e "s|__LOAD_CLIENT_IP__|10.0.0.20|g" \
    -e "s|__K8S_TOKEN__|$TOKEN|g" \
    Monitoring/prometheus/scrape_configs.yml > /tmp/crdp-jobs.yml
```

Append the rendered jobs to the **`scrape_configs:` list** of `prometheus.yml` on the Prometheus host.
They are already indented to sit directly under that key. Back the file up first:

```bash
scp /tmp/crdp-jobs.yml SSH_USER@PROM_HOST:/tmp/
ssh SSH_USER@PROM_HOST '
  sudo cp /etc/prometheus/prometheus.yml /etc/prometheus/prometheus.yml.bak.$(date +%F-%H%M%S)
  cat /tmp/crdp-jobs.yml | sudo tee -a /etc/prometheus/prometheus.yml >/dev/null
  rm -f /tmp/crdp-jobs.yml'
```

**Validate before reloading.** A malformed config stops Prometheus from starting:

```bash
ssh SSH_USER@PROM_HOST '
  sudo cp /etc/prometheus/prometheus.yml ~/check.yml && sudo chown $USER ~/check.yml
  sudo docker cp ~/check.yml prometheus:/tmp/check.yml
  sudo docker exec prometheus promtool check config /tmp/check.yml
  rm -f ~/check.yml'
```

Apply it:

```bash
# With --web.enable-lifecycle:
ssh SSH_USER@PROM_HOST 'curl -X POST http://localhost:9090/-/reload'
# Otherwise:
ssh SSH_USER@PROM_HOST 'sudo docker restart prometheus'
```

> The rendered file contains a credential. Delete it when done, and never commit it. `.gitignore`
> already excludes `*.token`, `k8s-token` and `scrape_configs.rendered.yml`.
>
> If Prometheus runs inside a container that bind-mounts only `prometheus.yml`, the token must be
> **inlined** as `authorization.credentials` (the template's default) because a sibling token file is not
> visible inside the container. If you bind-mount the whole `/etc/prometheus` directory, prefer
> `credentials_file: /etc/prometheus/k8s-token` (mode `0600`) so the secret stays out of the config.

Confirm every target is up:

```bash
curl -s http://PROM_HOST:9090/api/v1/targets \
  | python3 -c 'import sys,json; [print("%-26s %-8s %s" % (t["labels"]["job"], t["health"], t.get("lastError",""))) for t in json.load(sys.stdin)["data"]["activeTargets"]]'
```

For a three-node cluster you should see **20 targets across 10 jobs**: `node-exporter` 4 (three nodes plus
the load client), `cadvisor` 3, `kubelet` 3, `kube-proxy` 3, and one each of `etcd`, `kube-scheduler`,
`kube-controller-manager`, `kube-apiserver`, `kube-state-metrics` and `prometheus`.

### Step 5 — Add Prometheus to Grafana and install the dashboards

Full detail, including the CRDP dashboard's panels, is in
[`grafana/DASHBOARDS.md`](grafana/DASHBOARDS.md). The short version:

**Add the data source.** In Grafana, go to *Connections → Data sources → Add new data source →
Prometheus*. Set the URL to `http://PROM_HOST:9090` and the scrape interval to `5s`, then *Save & test*.

Use the host's IP or FQDN, **not `localhost`** — Grafana runs in its own container, where `localhost` is
Grafana itself, not Prometheus.

Equivalently, via the API:

```bash
curl -sS -u admin:'<password>' -H 'Content-Type: application/json' \
  -X POST http://PROM_HOST:3000/api/datasources \
  -d '{"name":"Prometheus","type":"prometheus","url":"http://PROM_HOST:9090",
       "access":"proxy","isDefault":true,"jsonData":{"timeInterval":"5s"}}'
```

**Import the CRDP dashboard.** *Dashboards → New → Import → Upload JSON file*, choose
[`grafana/crdp-kubernetes-dashboard.json`](grafana/crdp-kubernetes-dashboard.json), then select the
`Prometheus` data source when prompted.

**Import the community dashboards.** *Dashboards → New → Import*, enter the ID, *Load*, pick the data
source:

| ID | Dashboard | Covers |
| --- | --- | --- |
| **1860** | Node Exporter Full | CPU (including steal), memory, disk, network for every host |
| **13332** | kube-state-metrics v2 | pod phase, restarts, replica drift |

Import by ID needs outbound access to `grafana.com` from your browser or the Grafana host. If that host
is isolated, download the JSON elsewhere and use *Upload JSON file*.

### Step 6 — Optionally monitor the key manager

Scraping CipherTrust Manager confirms that the key manager is **not** on CRDP's per-transaction path —
its request rate should stay flat while CRDP throughput scales. It needs a non-expiring metrics token;
see [`prometheus/CIPHERTRUST_MANAGER_METRICS.md`](prometheus/CIPHERTRUST_MANAGER_METRICS.md) and
uncomment the last job in `scrape_configs.yml`.

---

## Verify the installation

```bash
# 1. Exporters answer, and no firewall blocks 9100 / 30080.
for ip in CONTROL_PLANE_IP WORKER_1_IP WORKER_2_IP LOAD_CLIENT_IP; do
  echo -n "$ip:9100 -> "; curl -s "http://$ip:9100/metrics" | grep -c '^node_cpu_seconds_total'
done
curl -s http://CONTROL_PLANE_IP:30080/metrics | grep -c '^kube_pod_info'

# 2. The token authorizes cAdvisor (expect Prometheus text, not 401).
curl -sk -H "Authorization: Bearer $TOKEN" \
     https://CONTROL_PLANE_IP:10250/metrics/cadvisor | head -3

# 3. CRDP pods survived the RKE2 restart: 24/24 Running, zero restarts.
$KUBECTL get pods -l run=crdp --no-headers | grep -c Running
$KUBECTL get pods -l run=crdp \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' \
  | awk '{s+=$1} END {print "restarts:", s}'

# 4. CRDP CPU is visible and near zero at idle.
curl -sG http://PROM_HOST:9090/api/v1/query --data-urlencode \
  'query=sum(rate(container_cpu_usage_seconds_total{job="cadvisor",pod=~"crdp-deployment-.*",container!=""}[1m]))'
```

### Cross-check against an independent measurement

Run a load test, then compare what cAdvisor reports against the node-level `/proc/stat` estimate that
`benchmark/aggregate_profile.py` computes. `benchmark/prom_snapshot.py` does both and prints the ratio:

```bash
export PROM_URL=http://PROM_HOST:9090
py benchmark/prom_snapshot.py --start <phase_start_epoch> --end <phase_end_epoch> \
    --rate-window 30s --tps <measured_txns_per_sec> --out results/run1/
```

Take the epochs from a client JSON's `wall_start_epoch` / `wall_end_epoch`. The two methods should agree
within about 10 %; the tool flags anything outside that as `DISAGREE -- investigate`.

Neither strictly bounds the other, so do not expect one to always read lower. The `/proc` estimate sweeps
in kubelet, containerd and CNI CPU **and counts steal as busy**, which inflates it; cAdvisor sees only
CRDP's cgroups.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `cadvisor` / `kubelet` target shows `401` | token missing, wrong, or RBAC not applied | re-run Step 2; re-render the config with a fresh `$TOKEN` |
| `etcd`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy` show `connection refused` | still bound to `127.0.0.1` | run Step 1 and confirm with `ss -lntp` |
| `rate(container_cpu_...)` returns no data over short windows | cAdvisor housekeeping interval too coarse | Step 1 sets `housekeeping-interval=5s`; use a `rate()` window ≥ 30 s |
| Node-level queries return an extra, unlabelled series | CipherTrust Manager publishes its own `node_cpu_seconds_total` | always filter `job="node-exporter"` |
| Prometheus will not start after an edit | malformed YAML | restore the `.bak` file; always run `promtool check config` first |
| `POST /-/reload` returns `405` | `--web.enable-lifecycle` not set | `docker restart prometheus`, or add the flag |
| Grafana data source test fails | `localhost` used inside a container | use the host IP or FQDN |
| kube-state-metrics HPA panels are empty | no HorizontalPodAutoscaler deployed | expected; not an error |

---

## Security notes

- **The ServiceAccount token and the CipherTrust Manager metrics token are credentials.** This repository
  contains only templates and procedures, never values. `.gitignore` covers `*.token`, `k8s-token` and
  `scrape_configs.rendered.yml`.
- **`etcd-expose-metrics: true` serves etcd metrics unauthenticated on `:2381`.** It reveals cluster
  topology and key counts, never key *values*. `node_exporter` (`:9100`), `kube-proxy` (`:10249`) and
  `kube-state-metrics` (`:30080`) are likewise unauthenticated. This is acceptable on a trusted, isolated
  network. On any shared network, restrict those ports to the Prometheus host — for example:

  ```bash
  sudo ufw allow from PROM_HOST to any port 9100,10249,10250,2381,10257,10259,30080 proto tcp
  ```

- **`insecure_skip_verify: true`** is set on the kubelet, scheduler, controller-manager and apiserver
  jobs because RKE2's serving certificates are self-signed per node. To verify them properly, mount the
  RKE2 CA (`/var/lib/rancher/rke2/server/tls/server-ca.crt`) into the Prometheus container and replace
  `insecure_skip_verify` with `ca_file`.
- **Prefer a scoped Grafana service-account token** (*Administration → Service accounts*, role `Editor`)
  over the admin password for any automation. Passing credentials to `curl -u` exposes them briefly in
  the process list; prefer `--netrc` or a token file.

## Cost

node_exporter is capped at 20m CPU / 64Mi per node, kube-state-metrics at 100m / 192Mi, with `requests`
equal to `limits` so neither can burst. On a three-node, 16-vCPU-per-node cluster that is roughly **0.1 %
of cluster CPU** — small, and measured rather than hidden.

## Reference

- [`promql/benchmark_queries.md`](promql/benchmark_queries.md) — the queries behind every panel, and what
  each one tells you about a benchmark run.
- [`grafana/DASHBOARDS.md`](grafana/DASHBOARDS.md) — dashboard contents and import notes.
- [`prometheus/CIPHERTRUST_MANAGER_METRICS.md`](prometheus/CIPHERTRUST_MANAGER_METRICS.md) — enabling the
  key manager's metrics endpoint.

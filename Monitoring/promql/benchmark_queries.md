# PromQL for CRDP benchmark analysis

Every query below replaces something the `benchmark/` shell samplers currently compute by hand.
Prometheus is at `http://192.168.1.186:9090`. The friendly `node` label (`kube`, `sphere`, `cone`,
`cm-neptune`) is attached by the scrape config, so you never have to remember which IP is which.

> **Always filter `job="node-exporter"` on `node_*` metrics.** The CipherTrust Manager
> (`job="CM-Kirk"`) embeds its own node exporter and publishes 32 `node_cpu_seconds_total` series for
> *its* host. Those series carry no `node` label, so a bare `node_cpu_seconds_total` silently mixes
> cm-kirk's CPU into cluster aggregations and adds a phantom unlabelled result. Same reasoning for
> `job="cadvisor"` on `container_*` metrics.

## 1. CPU steal — hypervisor oversubscription

This is the metric `benchmark/sample_steal.sh` extracts from `/proc/stat` field 8.

```promql
avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="steal"}[1m])) * 100
```

Steal is the share of time a vCPU was runnable but the hypervisor was running someone else. It is the
direct evidence for the report's central caveat: **a vCPU is not a physical core.** `cm-neptune` shares
hypervisor *Lemonade* with `cone`, so load-generation steals cycles from `cone`'s CRDP pods.

## 2. Node busy % — total host utilization

```promql
100 - avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[1m])) * 100
```

`aggregate_profile.py` uses this (as `busy_pct`) to decide whether a run **saturated** the backend
(`peak_busy >= 60%`) or was **client-limited**. The binary-protect run sat at ~16% busy, which is why
its 134,591 tps/core is reported as a lower bound rather than a ceiling.

## 3. CRDP pod CPU — cores actually consumed  ← the important one

```promql
# total cores across the deployment
sum(rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# broken out per node
sum by (node) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# per pod, to check the topology spread is actually balancing load
sum by (pod) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))
```

`container_cpu_usage_seconds_total` is a **counter** scraped from cAdvisor, so `rate()` over it is exact
and has no reporting lag. Two things this fixes:

- **`kubectl top` / metrics-server lags 15–25 s.** That lag silently corrupted the first aggregation
  attempt: a 39-core peak appeared *after* the load had already stopped, yielding `cores=1.0` for
  PROTECT and nonsense efficiency. `benchmark/sample_backend.sh` exists only as a workaround.
- **It replaces an estimate with a measurement.** `aggregate_profile.py:89-101` infers backend cores as
  `(busy% − idle_baseline%) × 16 vCPU` — attributing *all* load-induced node CPU to CRDP. The cAdvisor
  query isolates the CRDP cgroups.

Filter `container!=""` drops the pod-level cgroup rollup that cAdvisor also emits, which would otherwise
double-count every pod.

> **Use a `rate()` window of at least 30 s, and make sure the kubelet runs with
> `--housekeeping-interval=5s`.** cAdvisor attaches its own timestamp to each sample and Prometheus
> honors it, so the effective resolution is the housekeeping interval, *not* the scrape interval — 10 s
> by default (~12.6 s as measured here). Before tuning, `rate(...[15s])` over a 22 s PROTECT phase
> returned data for only 2 of 24 pods, because a pod needs two **distinct** samples inside the window.
> `Monitoring/rke2/apply_rke2_metrics.sh` sets the interval; the scrape job sets `honor_timestamps: false`.

**How the two methods compare.** On an 8-client digits run they agree closely — PROTECT 44.3 cores
(cAdvisor) vs 42.4 (`/proc` estimate) against 43.5 in `results/report.md`; REVEAL 36.7 vs 36.6. Neither
strictly bounds the other, so don't assume cAdvisor reads lower: the estimate includes
kubelet/containerd/canal CPU **and counts steal as busy**, both of which inflate it, while cAdvisor sees
only CRDP. Treat a gap under ~10 % as agreement.

## 4. Efficiency — the sizing input

The report's whole recommendation reduces to `txns/sec ÷ backend cores`. Transactions come from the
client JSONs (CRDP exposes no `/metrics`), cores from query 3:

```promql
sum(rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))
```

Divide the run's overlapped-window txns/sec by the mean of this over the steady window. The gating
profile (alphanumeric PROTECT) measured 5,377 tps/core, which is what drives the 186-core / 14-node
recommendation.

## 5. Sanity checks

```promql
# every scrape target healthy
up == 0

# pods per node — should be 8/8/8 with maxSkew=1
count by (node) (kube_pod_info{pod=~"crdp-deployment-.*"})

# the 1500m CPU request from crdp-app-svc-ing.yml, confirmed from the live cluster
kube_pod_container_resource_requests{pod=~"crdp-deployment-.*", resource="cpu"}

# pods restarting? a restart mid-run invalidates the run
sum(kube_pod_container_status_restarts_total{pod=~"crdp-deployment-.*"})

# memory headroom on the load client (15 GiB; iterations, not batch size, drive RSS)
node_memory_MemAvailable_bytes{node="cm-neptune"} / 1024^3
```

## 6. Control plane

Available only after `Monitoring/rke2/apply_rke2_metrics.sh` has moved these listeners off 127.0.0.1.

```promql
# etcd write latency (p99) — watch during heavy pod churn, not steady load
histogram_quantile(0.99, rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m]))

# apiserver request rate by verb
sum by (verb) (rate(apiserver_request_total[1m]))

# scheduler backlog
scheduler_pending_pods
```

The control plane is *not* expected to move during a CRDP benchmark — the pod set is static and the
data path never touches the apiserver. These are here to **prove** that, so control-plane overhead can
be ruled out as an explanation for a throughput ceiling.

## Querying from a script

`benchmark/prom_snapshot.py` wraps `/api/v1/query_range` and emits the same JSONL the aggregator already
reads, so it is a drop-in replacement for the shell samplers. Pass the **phase** window straight from a
client JSON's `wall_start_epoch` / `wall_end_epoch` — don't pre-pad it, `--pad` does that:

```bash
py benchmark/prom_snapshot.py --start 1783622612 --end 1783622653 \
    --rate-window 30s --tps 512615 --out results/digits/
```

`--pad` widens only the emitted JSONL, so `aggregate_profile.py` can still find an idle baseline
(`min(busy_pct)` across the file). The printed summary skips exactly one rate window from the start of
the phase, because `rate()` looks backward and the earliest in-phase samples still average in pre-load
idle. Trimming a fixed *fraction* of rows does not fix that; trimming one rate window does.

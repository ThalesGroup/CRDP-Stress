# PromQL for CRDP benchmark analysis

Every query below answers a specific question about a CRDP load test. Each is also a panel on the
dashboard in [`../grafana/crdp-kubernetes-dashboard.json`](../grafana/crdp-kubernetes-dashboard.json).

The `node` label (`control-plane`, `worker-1`, `load-client`, …) is attached by the scrape config, so you
never have to remember which IP is which.

> **Always filter `job="node-exporter"` on `node_*` metrics.** CipherTrust Manager exports its own
> embedded node exporter and publishes `node_cpu_seconds_total` series for *its* host. Those series carry
> no `node` label, so a bare `node_cpu_seconds_total` silently mixes the key manager's CPU into
> cluster-wide aggregations and adds a phantom unlabelled result. The same reasoning applies to
> `job="cadvisor"` on `container_*` metrics.

---

## 1. CPU steal — hypervisor oversubscription

```promql
avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="steal"}[1m])) * 100
```

Steal is the share of time a vCPU was runnable but the hypervisor was running someone else. It is the
direct evidence for a claim that governs all capacity planning: **a vCPU is not a physical core.**

This matters most when the load-generating client shares a hypervisor with a cluster node — driving load
then steals cycles from that node's own CRDP pods, and per-core efficiency comes out lower than the
hardware can actually deliver. Sustained steal above a few percent means your sizing figures are
pessimistic; size on dedicated physical cores.

## 2. Node busy % — is the backend saturated?

```promql
100 - avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[1m])) * 100
```

Use this to decide whether a run is **backend-bound** (the real ceiling) or **client-limited**. If the
backend nodes sit well below full while throughput plateaus, a single load host could not saturate the
cluster, and the measured efficiency is a *lower bound* rather than a per-core ceiling.

Pair it with the load client's own utilization:

```promql
100 - avg(rate(node_cpu_seconds_total{job="node-exporter", node="load-client", mode="idle"}[1m])) * 100
```

If the backend is pegged and the client has headroom, you are measuring CRDP. If the client is pegged,
you are measuring your load generator.

## 3. CRDP pod CPU — the cores actually consumed

```promql
# total cores across the deployment
sum(rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# broken out per node
sum by (node) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# per pod, to check that pod spreading is actually balancing load
sum by (pod) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))
```

`container_cpu_usage_seconds_total` is a **counter** scraped from cAdvisor, so `rate()` over it is exact
and has no reporting lag — unlike metrics-server (`kubectl top`), which lags 15–25 seconds and is
unusable for attributing CPU to a short load phase.

The filter `container!=""` drops the pod-level cgroup rollup that cAdvisor also emits, which would
otherwise double-count every pod.

> **Use a `rate()` window of at least 30 s, and run the kubelet with `--housekeeping-interval=5s`.**
> cAdvisor attaches its own timestamp to each sample and Prometheus honors it, so the effective
> resolution is the housekeeping interval, *not* the scrape interval — 10 s by default. At that cadence,
> `rate(...[15s])` over a 22-second phase returns data for only a handful of pods, because a pod needs two
> **distinct** samples inside the window. `../rke2/apply_rke2_metrics.sh` sets the interval, and the
> scrape job sets `honor_timestamps: false`.

## 4. Efficiency — the number that drives sizing

Capacity planning reduces to **transactions/sec ÷ backend cores**. Transaction counts come from the
stress client's JSON output (CRDP exposes no `/metrics` of its own); cores come from query 3:

```promql
sum(rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))
```

Divide the run's sustained transactions/sec by the mean of this over the steady window. To reach a target
throughput, `required_cores = target_tps / measured_tps_per_core`. Size to the **least efficient**
protection policy you must support — different ciphers and field lengths differ by several times in cost
per transaction.

### Cross-checking against the node-level estimate

`benchmark/aggregate_profile.py` estimates backend cores as `(busy% − idle_baseline%) × vCPU_per_node`.
`benchmark/prom_snapshot.py` computes both that estimate and the cAdvisor measurement, and prints their
ratio. Expect agreement within about 10 %.

Neither strictly bounds the other. The `/proc`-based estimate sweeps in kubelet, containerd and CNI CPU,
**and counts steal as busy**, both of which inflate it; cAdvisor sees only CRDP's cgroups. A gap larger
than ~10 % means the core-attribution method needs review.

## 5. Sanity checks

```promql
# any scrape target down
up == 0

# pods per node — should be even if topologySpreadConstraints are working
count by (node) (kube_pod_info{pod=~"crdp-deployment-.*"})

# the CPU request from the deployment manifest, confirmed against the live cluster
kube_pod_container_resource_requests{pod=~"crdp-deployment-.*", resource="cpu"}

# a pod restart mid-run invalidates the run
sum(kube_pod_container_status_restarts_total{pod=~"crdp-deployment-.*"})

# memory headroom on the load client (per-client iterations, not batch size, drive RSS)
node_memory_MemAvailable_bytes{job="node-exporter", node="load-client"} / 1024^3
```

## 6. Control plane

Available only after `../rke2/apply_rke2_metrics.sh` has moved these listeners off `127.0.0.1`.

```promql
# etcd write latency (p99)
histogram_quantile(0.99, sum by (le) (rate(etcd_disk_wal_fsync_duration_seconds_bucket[5m])))

# apiserver request rate by verb
sum by (verb) (rate(apiserver_request_total[1m]))

# scheduler backlog
sum(scheduler_pending_pods)
```

The control plane is *not* expected to move during a CRDP benchmark: the pod set is static and the data
path never touches the apiserver. These queries exist to **prove** that, so control-plane overhead can be
ruled out as an explanation for a throughput ceiling.

## Querying from a script

`benchmark/prom_snapshot.py` wraps `/api/v1/query_range` and writes the same JSONL that
`benchmark/aggregate_profile.py` reads, so it is a drop-in replacement for the in-band shell samplers.
Pass the **phase** window straight from a client JSON's `wall_start_epoch` / `wall_end_epoch`; do not
pre-pad it, because `--pad` does that:

```bash
export PROM_URL=http://PROM_HOST:9090
py benchmark/prom_snapshot.py --start 1783622612 --end 1783622653 \
    --rate-window 30s --tps 512615 --out results/digits/
```

Two windowing subtleties are handled for you:

- `--pad` widens only the **emitted JSONL**, so `aggregate_profile.py` can still derive each node's idle
  baseline from `min(busy_pct)` across the file. Without idle samples on either side of the load, that
  baseline collapses to the loaded value and the estimated core count falls to near zero.
- The printed summary skips exactly **one rate window** from the start of the phase. `rate()` looks
  backward, so the earliest in-phase samples still average in pre-load idle. Trimming a fixed *fraction*
  of rows does not fix this; trimming one rate window does.

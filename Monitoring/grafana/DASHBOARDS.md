# Grafana on the Prometheus host

Grafana **already runs** on `192.168.1.186` — container `grafana`, image `grafana/grafana-enterprise`
v11.1.0, published on `0.0.0.0:3000`, state persisted in the named volume `grafana-storage`. It also
**already had a Prometheus datasource** configured and set as default:

| Field | Value |
| --- | --- |
| Name | `Prometheus` |
| UID | `fds29p01wk2yob` |
| URL | `http://prometheus.test256.io:9090` |
| Default | yes |

So nothing needed installing or wiring. Everything the exporters publish became queryable in
**Grafana → Explore** the moment the scrape jobs went live. What was missing was somewhere to *render*
it: of the 8 pre-existing dashboards, every one covered CipherTrust Manager or Prometheus itself, and
none touched Kubernetes or CRDP.

## Dashboards (all imported and verified)

| Dashboard | URL | Source |
| --- | --- | --- |
| **CRDP / Kubernetes — Benchmark & Health** | `/d/crdp-k8s-bench` | `crdp-kubernetes-dashboard.json` (this repo) |
| Node Exporter Full | `/d/rYdddlPWk` | grafana.com ID **1860** |
| kube-state-metrics-v2 | `/d/garysdevil-kube-state-metrics-v2` | grafana.com ID **13332** |

Import-by-ID works from this host (`grafana.com` is reachable). To import the local one by hand:
*Dashboards → New → Import → Upload JSON file*, then pick the `Prometheus` datasource.

Notes from the import, so nobody re-treads them:

- The JSON ships with `__inputs` (standard Grafana export format), but Grafana's
  `POST /api/dashboards/import` did **not** substitute `${DS_PROMETHEUS}` when the payload also
  stripped `__inputs`. Substituting the datasource UID literally and posting to
  `POST /api/dashboards/db` is deterministic and works.
- **Node Exporter Full's** `job` selector offers both `node-exporter` (kube, sphere, cone, cm-neptune)
  **and `CM-Kirk`** — the CipherTrust Manager exports its own host metrics, so you get its CPU, memory
  and disk for free. Pick the job first, then the host.
- **kube-state-metrics-v2's** HPA panels read empty. That is correct, not broken: this deployment runs
  **no Horizontal Pod Autoscaler**. Its `cluster` template variable is also empty, because
  `kube_node_info` carries no `cluster` label in a single-cluster install; the panels still resolve
  because `cluster=~""` matches series lacking the label.

## The CRDP dashboard

Four rows, 19 panels, every query verified against live Prometheus. It shows exactly the quantities
`results/report.md` is built from.

**CRDP workload (cAdvisor)** — cores in use (the sizing input; thresholds turn red past 43, the figure
the report measured for digits PROTECT), pods Running, container restarts (a restart mid-run invalidates
a benchmark), cores by node, CPU per pod across all 24 pods, memory working set.

**Saturation & CPU steal (node_exporter)** — node busy %, CPU steal %, a gauge for the load client
cm-neptune, and its available memory. Read the last two together, because this is the question the whole
benchmark turns on:

> If node busy is pegged and cm-neptune has headroom, **the backend is the wall** — you are measuring
> CRDP. If cm-neptune is pegged, you are measuring the load generator.

Steal matters because a vCPU is not a physical core: cm-neptune shares hypervisor *Lemonade* with node
`cone`, so generating load steals cycles from `cone`'s own CRDP pods. A measured run showed `cone` at
**7.26 % steal** during REVEAL while `kube` and `sphere` sat near zero.

**RKE2 control plane** — etcd WAL fsync p99, apiserver request rate by verb, scheduler backlog and
controller-manager queue depth. These are here to **prove a negative**: the CRDP data path never touches
the apiserver and the pod set is static, so these should stay flat under load. If they don't, the
control plane is interfering and the throughput numbers are suspect.

**Scrape health** — targets up by job. Should sit flat at **20**: node-exporter 4, cadvisor 3, kubelet 3,
kube-proxy 3, and one each for etcd, kube-scheduler, kube-controller-manager, kube-apiserver,
kube-state-metrics, CM-Kirk, and prometheus.

### Why `rate(...[1m])` and not `$__rate_interval`

cAdvisor stamps its own timestamps and Prometheus honors them, so the effective resolution is the
kubelet's cAdvisor **housekeeping interval**, not the scrape interval. Grafana's `$__rate_interval` can
shrink below that on a zoomed-in time range and silently return **no data**. The panels therefore
hardcode `[1m]`. See `../promql/benchmark_queries.md`.

## Queries you can paste into Explore right now

```promql
# CRDP cores in use, per node
sum by (node) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# Is the backend saturated?
100 - avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[1m])) * 100

# A vCPU is not a physical core
avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="steal"}[1m])) * 100
```

Always filter `job="node-exporter"` on `node_*` metrics — see the warning in
`../promql/benchmark_queries.md`; CipherTrust Manager publishes colliding series.

## Credentials

The Grafana admin password is **not** stored in this repo and must never be committed. Prefer a scoped
service-account token (*Administration → Service accounts*, role `Editor`) over the admin password for
any automated import; a token is revocable and least-privilege. Note that passing credentials to `curl -u`
on the host exposes them briefly in that process's argv (`ps`); prefer `--netrc` or a token in a file.

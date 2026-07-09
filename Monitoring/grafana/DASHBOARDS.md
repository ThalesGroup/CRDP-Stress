# Grafana dashboards

Three dashboards give full coverage of a CRDP deployment: one purpose-built for CRDP (included in this
repository) and two community dashboards for host and Kubernetes object detail.

Grafana is not included with RKE2 or CRDP. If you do not already run it, see Step 0 of
[`../README.md`](../README.md).

## 1. Add Prometheus as a Grafana data source

*Connections → Data sources → Add new data source → Prometheus.*

| Field | Value |
| --- | --- |
| Name | `Prometheus` |
| URL | `http://PROM_HOST:9090` |
| Scrape interval | `5s` |

Use the host's IP or FQDN, **not `localhost`**. Grafana runs in its own container, where `localhost`
resolves to Grafana itself rather than to Prometheus. Click *Save & test* — it should report
"Successfully queried the Prometheus API."

By API:

```bash
curl -sS -u admin:'<password>' -H 'Content-Type: application/json' \
  -X POST http://PROM_HOST:3000/api/datasources \
  -d '{"name":"Prometheus","type":"prometheus","url":"http://PROM_HOST:9090",
       "access":"proxy","isDefault":true,"jsonData":{"timeInterval":"5s"}}'
```

## 2. Install the dashboards

| Dashboard | Source | How |
| --- | --- | --- |
| **CRDP / Kubernetes — Benchmark & Health** | `crdp-kubernetes-dashboard.json` (this repo) | *Dashboards → New → Import → Upload JSON file* |
| Node Exporter Full | grafana.com ID **1860** | *Dashboards → New → Import*, enter `1860`, *Load* |
| kube-state-metrics v2 | grafana.com ID **13332** | *Dashboards → New → Import*, enter `13332`, *Load* |

Select the `Prometheus` data source when prompted. Import-by-ID requires outbound access to
`grafana.com`; if the host is isolated, download the JSON elsewhere and use *Upload JSON file*.

### Importing by API

`POST /api/dashboards/import` will **not** substitute the `${DS_PROMETHEUS}` placeholder if the payload
has had its `__inputs` block stripped — the dashboard imports "successfully" but every panel points at a
data source that does not exist. Substituting the data-source UID literally and posting to
`POST /api/dashboards/db` is deterministic:

```bash
DS_UID=$(curl -s -u admin:'<password>' http://PROM_HOST:3000/api/datasources \
         | python3 -c 'import sys,json;print(next(d["uid"] for d in json.load(sys.stdin) if d["type"]=="prometheus"))')

python3 - "$DS_UID" <<'PY'
import json, sys, base64, urllib.request
raw = open("crdp-kubernetes-dashboard.json").read().replace("${DS_PROMETHEUS}", sys.argv[1])
d = json.loads(raw)
d.pop("__inputs", None); d.pop("__requires", None); d["id"] = None
body = json.dumps({"dashboard": d, "overwrite": True, "folderId": 0}).encode()
req = urllib.request.Request("http://PROM_HOST:3000/api/dashboards/db", data=body, headers={
    "Content-Type": "application/json",
    "Authorization": "Basic " + base64.b64encode(b"admin:<password>").decode()})
print(json.load(urllib.request.urlopen(req)))
PY
```

Afterwards, confirm no placeholders survived:

```bash
curl -s -u admin:'<password>' http://PROM_HOST:3000/api/dashboards/uid/crdp-k8s-bench \
  | grep -c 'DS_PROMETHEUS'      # expect 0
```

Prefer a scoped service-account token (*Administration → Service accounts*, role `Editor`) over the
admin password for automation.

## 3. What the CRDP dashboard shows

Four rows, 19 panels. A `pod_regex` template variable (default `crdp-deployment-.*`) selects the pods.

**CRDP workload (cAdvisor)** — CPU cores in use, pods Running, container restarts, cores by node, CPU per
pod, and memory working set.

*Cores in use* is the sizing input: divide measured transactions/sec by it to get transactions per core,
which is what tells you how many cores a target throughput requires. *CPU per pod* reveals whether
`topologySpreadConstraints` and Service round-robin are actually balancing work — a wide spread means
they are not. *Container restarts* should stay at zero; a restart mid-run invalidates a benchmark.

**Saturation & CPU steal (node_exporter)** — node busy %, CPU steal %, a gauge for the load client, and
its available memory.

Read the last two together, because this is the question a benchmark turns on:

> If node busy is pegged and the load client has headroom, **the backend is the wall** — you are
> measuring CRDP. If the load client is pegged, you are measuring your load generator.

Steal matters because **a vCPU is not a physical core**. When a hypervisor oversubscribes physical cores,
a guest can be runnable while another guest runs. If your load client shares a hypervisor with a cluster
node, generating load literally steals cycles from that node's CRDP pods. Sustained steal above a few
percent means throughput and per-core efficiency figures understate the hardware.

**RKE2 control plane** — etcd WAL fsync p99, apiserver request rate by verb, scheduler backlog and
controller-manager queue depth.

These exist to **prove a negative**. CRDP's data path never touches the apiserver, and the pod set is
static during a run, so these should stay flat under load. If they move, the control plane is interfering
and the throughput numbers are suspect.

**Scrape health** — targets up by job. On a three-node cluster this sits flat at **20**.

### Why `rate(...[1m])` and not `$__rate_interval`

cAdvisor stamps its own timestamps and Prometheus honors them, so the effective resolution is the
kubelet's cAdvisor **housekeeping interval**, not the scrape interval. Grafana's `$__rate_interval`
shrinks on a zoomed-in time range and can silently return **no data**. The panels therefore hardcode
`[1m]`. See [`../promql/benchmark_queries.md`](../promql/benchmark_queries.md).

## 4. Notes on the community dashboards

**Node Exporter Full (1860)** — pick the `job` first, then the host. If you also scrape CipherTrust
Manager, its job appears in the same selector, because CipherTrust Manager exports host metrics of its
own. That is a convenience, but it is also why every node-level query in this repository filters on
`job="node-exporter"`: without the filter, the key manager's CPU silently contaminates cluster-wide
aggregations.

**kube-state-metrics v2 (13332)** — its HPA panels read empty unless you deploy a HorizontalPodAutoscaler.
That is correct, not broken. Its `cluster` template variable is also empty on a single-cluster install,
because `kube_node_info` carries no `cluster` label; panels still resolve, since `cluster=~""` matches
series that lack the label.

## 5. Queries you can paste into Explore

```promql
# CRDP cores in use, per node
sum by (node) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# Is the backend saturated?
100 - avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[1m])) * 100

# A vCPU is not a physical core
avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="steal"}[1m])) * 100
```

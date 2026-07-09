# Grafana on the Prometheus host

Grafana is **already running** on `192.168.1.186` — container `grafana`, image
`grafana/grafana-enterprise`, published on `0.0.0.0:3000`, state persisted in the named volume
`grafana-storage`. Nothing needs installing; only a datasource and dashboards.

Because it was started with `docker run` (no provisioning directory is bind-mounted), the datasource
must be added through the UI or HTTP API rather than a provisioning YAML.

## 1. Add the Prometheus datasource

Browse to `http://192.168.1.186:3000` → *Connections → Data sources → Add → Prometheus*.

| Field | Value |
| --- | --- |
| Name | `Prometheus` |
| URL | `http://192.168.1.186:9090` |
| Scrape interval | `5s` |

Use the **host IP, not `localhost`** — Grafana is in its own bridge-network container, so `localhost`
resolves to the Grafana container, not the Prometheus one.

Equivalent API call (substitute the real admin password):

```bash
curl -sS -u admin:'<password>' -H 'Content-Type: application/json' \
  -X POST http://192.168.1.186:3000/api/datasources \
  -d '{"name":"Prometheus","type":"prometheus","url":"http://192.168.1.186:9090",
       "access":"proxy","isDefault":true,"jsonData":{"timeInterval":"5s"}}'
```

## 2. Import dashboards

*Dashboards → New → Import → paste ID → Load → pick the Prometheus datasource.*

| ID | Dashboard | Covers |
| --- | --- | --- |
| **1860** | Node Exporter Full | CPU (incl. **steal**), memory, disk, network for all four hosts |
| **315** | Kubernetes cluster monitoring | cluster-wide CPU/memory from cAdvisor |
| **13332** | kube-state-metrics v2 | pod phase, restarts, replica drift |
| **3662** | Prometheus 2.0 overview | scrape health, TSDB size |

Import by ID needs outbound internet from `192.168.1.186` to `grafana.com`. If that host is isolated,
download the JSON on a machine that has access and use *Import → Upload JSON file*.

On dashboard 1860, the host selector is driven by the `instance` label (`192.168.1.188:9100`, …). The
scrape config also attaches a friendly `node` label (`kube`, `sphere`, `cone`, `cm-neptune`) — prefer
`node` when you write your own panels.

## 3. A CRDP benchmark dashboard

None of the stock dashboards know about CRDP, and CRDP exposes no `/metrics` of its own (its container
declares only port 8090; `/metrics`, `/v1/metrics` and `/actuator/prometheus` all return the app's 404).
Everything below therefore comes from cAdvisor and node_exporter. Build one dashboard with four panels —
these are exactly the quantities the throughput report is built from:

```promql
# Panel 1 — CRDP cores consumed, per node. Compare against 16 vCPU/node.
sum by (node) (rate(container_cpu_usage_seconds_total{job="cadvisor", pod=~"crdp-deployment-.*", container!=""}[1m]))

# Panel 2 — CPU steal %. The evidence that a vCPU is not a physical core.
avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="steal"}[1m])) * 100

# Panel 3 — node busy %. Is the backend saturated, or is the run client-limited?
100 - avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="idle"}[1m])) * 100

# Panel 4 — load client headroom. Proves cm-neptune is not the bottleneck.
100 - avg(rate(node_cpu_seconds_total{job="node-exporter", node="cm-neptune", mode="idle"}[1m])) * 100
```

Panels 3 and 4 side by side answer the question the whole benchmark hinges on: if panel 3 is pegged and
panel 4 has headroom, the backend is the wall. If panel 4 is pegged, you are measuring the load
generator, not CRDP.

See `../promql/benchmark_queries.md` for the full query reference.

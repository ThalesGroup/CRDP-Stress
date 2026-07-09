# Scraping CipherTrust Manager

CipherTrust Manager — the key manager CRDP registers against — can publish Prometheus metrics from
`/api/v1/system/metrics/prometheus`. Scraping it is optional but recommended.

## Why bother

It lets you **prove the key manager is not on CRDP's per-transaction path.** CRDP pods register with the
key manager and fetch an auth token at startup, not per request, so key-manager request rate and latency
should stay flat while CRDP throughput scales. Without this job that is an inference; with it, an
observation. If key-manager load *does* track CRDP throughput, something is wrong with the deployment.

CipherTrust Manager also runs an embedded node exporter, so scraping it yields the CPU, memory and disk of
the key manager's own host for free.

## Enable the endpoint and mint a token

The metrics endpoint is **off by default**. Run these against CipherTrust Manager as an administrator.
Subcommand spelling varies slightly across versions — check `ksctl metrics --help` first.

```bash
# 1. Is the metrics endpoint enabled?
ksctl metrics status

# 2. Enable it if not.
ksctl metrics enable

# 3. Mint the metrics token.
ksctl metrics prometheus renew-token
```

Equivalent REST calls. Note that step 3 is authorized with an ordinary admin API token, which is a
*different*, short-lived kind of token (see below):

```bash
CM=https://<key-manager-host>

JWT=$(curl -sk -X POST "$CM/api/v1/auth/tokens" \
        -H 'Content-Type: application/json' \
        -d '{"grant_type":"password","username":"admin","password":"<password>"}' \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["jwt"])')

curl -sk -X POST "$CM/api/v1/system/metrics/prometheus/renew-token" \
     -H "Authorization: Bearer $JWT"
```

## Two kinds of token — the source of nearly every 401

CipherTrust Manager issues two bearer tokens that behave in opposite ways:

| Token | Lifetime | Used for |
| --- | --- | --- |
| API token (JWT), from `POST /api/v1/auth/tokens` | **5 minutes** | general REST API calls |
| **Prometheus metrics token** | **does not expire** | *only* `/api/v1/system/metrics/prometheus` |

Because the metrics token never expires, **a `401` on the scrape job means the token was invalidated, not
that it aged out.** In order of likelihood:

1. The metrics endpoint was disabled.
2. The token was renewed — renewing mints a new token and immediately invalidates the previous one, so
   every Prometheus scraping this key manager must be updated at the same time.
3. CipherTrust Manager was rebuilt or redeployed, so the stored token belongs to a previous instance.

> If a 5-minute API JWT is pasted into `bearer_token` by mistake, the job scrapes successfully for about
> five minutes and then returns `401` forever. Same symptom, different bug. The procedure above always
> yields the correct, non-expiring token type.

## Configure the scrape job

Uncomment the last job in [`scrape_configs.yml`](scrape_configs.yml) and fill in the host and token:

```yaml
  - job_name: "ciphertrust-manager"
    scheme: https
    metrics_path: /api/v1/system/metrics/prometheus
    tls_config:
      insecure_skip_verify: true
    bearer_token: "<metrics-token>"
    static_configs:
      - targets: ["<key-manager-host>"]
```

`insecure_skip_verify: true` skips TLS validation and is **not** recommended for production. To verify
properly, supply the CA that signed the key manager's certificate:

```yaml
    tls_config:
      ca_file: "/etc/prometheus/ciphertrust-ca.pem"
      server_name: "<key-manager-fqdn>"
```

Apply the change:

```bash
# With --web.enable-lifecycle:
curl -X POST http://<prometheus-host>:9090/-/reload
# Otherwise:
sudo docker restart prometheus
```

## Verify

```bash
# The endpoint answers with Prometheus text, not 401.
curl -k 'https://<key-manager-host>/api/v1/system/metrics/prometheus' \
     -H 'Authorization: Bearer <metrics-token>' --compressed | head

# Prometheus agrees the target is up.
curl -s 'http://<prometheus-host>:9090/api/v1/targets' \
  | python3 -c 'import sys,json; [print(t["labels"]["job"], t["health"], t.get("lastError","")) for t in json.load(sys.stdin)["data"]["activeTargets"]]'
```

## A metric-name collision to be aware of

Because CipherTrust Manager embeds a node exporter, it publishes its own `node_cpu_seconds_total` series.
Those series carry no `node` label, so any node-level query that does not filter by job will silently mix
the key manager's CPU into cluster-wide aggregations:

```promql
# wrong: includes the key manager's host
avg by (node) (rate(node_cpu_seconds_total{mode="steal"}[1m])) * 100

# right
avg by (node) (rate(node_cpu_seconds_total{job="node-exporter", mode="steal"}[1m])) * 100
```

## Security

The metrics token is a credential, and it does not expire. Never commit it. `.gitignore` covers `*.token`
and `k8s-token`; this repository holds only the procedure, never a value.

Prometheus reads its configuration file as whatever user its process runs as, so an inlined
`bearer_token` must be readable by that user. If you bind-mount the whole `/etc/prometheus` directory into
the container, prefer `bearer_token_file` with mode `0600` so the secret never appears in the config.

## Sources

- [Prometheus Metrics Endpoint — CipherTrust Manager](https://thalesdocs.com/ctp/cm/2.18/admin/cm_admin/monitoring/metrics/index.html)
- [Authentication Tokens — CipherTrust Manager](https://thalesdocs.com/ctp/cm/2.18/admin/cm_admin/authentication/tokens/index.html)

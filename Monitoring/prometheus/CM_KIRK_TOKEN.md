# Fixing the `CM-Kirk` scrape job (HTTP 401)

## Symptom

`prometheus.test256.io` has scraped the CipherTrust Manager (`cm-kirk.test256.io`, 192.168.1.190)
since before this branch, via the job below in `/etc/prometheus/prometheus.yml`:

```yaml
  - job_name: "CM-Kirk"
    scheme: "https"
    tls_config:
      insecure_skip_verify: true
    bearer_token: "<redacted>"
    metrics_path: "/api/v1/system/metrics/prometheus"
    static_configs:
      - targets: ["cm-kirk.test256.io"]
```

The target is **DOWN with `server returned HTTP status 401 Unauthorized`**.

## Root cause (not what it looks like)

The obvious guess is that the token timed out. It didn't. CipherTrust Manager has **two different
kinds of bearer token**, and they behave oppositely:

| Token | Lifetime | Used for |
| --- | --- | --- |
| API token (JWT), from `POST /api/v1/auth/tokens` | **5 minutes** | general REST API calls |
| **Prometheus metrics token** | **does not expire** | *only* `/api/v1/system/metrics/prometheus` |

Because the metrics token never expires, a 401 means the token was **invalidated**, not aged out.
The realistic causes, in order:

1. **The metrics endpoint was disabled** on cm-kirk (it is off by default).
2. **The token was renewed** — renewing mints a new token and immediately invalidates the old one.
3. **cm-kirk was rebuilt/re-deployed**, so the stored token belongs to a previous instance.

Given the CRDP environment was recently torn down and redeployed, (3) is the most likely.

> If someone ever pastes a 5-minute API JWT into `bearer_token`, the job will scrape successfully for
> about five minutes and then start 401ing forever. That is a different bug with the same symptom.
> The fix below always yields the correct, non-expiring token type.

## Fix

Run against cm-kirk as a CipherTrust Manager admin. The exact subcommand spelling varies across CM
versions — check `ksctl metrics --help` first.

```bash
# 1. Is the metrics endpoint even enabled?
ksctl metrics status

# 2. Enable it if not.
ksctl metrics enable

# 3. Mint a fresh, non-expiring metrics token. This INVALIDATES the previous one,
#    so any other Prometheus scraping cm-kirk must be updated at the same time.
ksctl metrics prometheus renew-token
```

Equivalent REST call, if you prefer curl (needs a normal 5-minute admin API JWT to authorize it):

```bash
CM=https://cm-kirk.test256.io
JWT=$(curl -sk -X POST "$CM/api/v1/auth/tokens" \
        -H 'Content-Type: application/json' \
        -d '{"grant_type":"password","username":"admin","password":"<password>"}' \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["jwt"])')

curl -sk -X POST "$CM/api/v1/system/metrics/prometheus/renew-token" \
     -H "Authorization: Bearer $JWT"
```

Then install the returned token on the Prometheus host and restart:

```bash
# on 192.168.1.186
sudo cp /etc/prometheus/prometheus.yml /etc/prometheus/prometheus.yml.bak.$(date +%F-%H%M%S)
sudo sed -i 's|^\(    bearer_token: \).*|\1"<NEW_METRICS_TOKEN>"|' /etc/prometheus/prometheus.yml
sudo docker restart "$(sudo docker ps -q --filter ancestor=prom/prometheus)"
```

`--web.enable-lifecycle` is not set on that container, so `POST /-/reload` will not work; a restart is
required. The TSDB is on a named volume, so no samples are lost.

## Verify

```bash
# Endpoint answers with Prometheus text, not 401:
curl -k 'https://cm-kirk.test256.io/api/v1/system/metrics/prometheus' \
     -H 'Authorization: Bearer <NEW_METRICS_TOKEN>' --compressed | head

# Prometheus agrees the target is up:
curl -s 'http://192.168.1.186:9090/api/v1/targets' \
  | python3 -c 'import sys,json; [print(t["labels"]["job"], t["health"], t.get("lastError","")) for t in json.load(sys.stdin)["data"]["activeTargets"]]'
```

## Why bother

cm-kirk is the key manager, and it was a **blind spot** during the recommendation-#3 investigation into
per-card cost. We ruled the key manager out of the CRDP hot path by indirect evidence (startup-only
registration, cold-then-cached latency, no improvement from larger batches). With this job UP, that
becomes directly observable: CM request rates and latency should stay flat while CRDP throughput
scales, confirming the KM is not on the per-transaction path.

## Security note

The token is a credential. It lives in `/etc/prometheus/prometheus.yml` (mode 0644, owner `rrobinson`)
because the Prometheus container runs as `nobody` and must be able to read it. **Never commit it.**
`.gitignore` covers `*.token` and `k8s-token`; this repo holds only the procedure, never the value.

## Sources

- [Prometheus Metrics Endpoint — CipherTrust Manager 2.18](https://thalesdocs.com/ctp/cm/2.18/admin/cm_admin/monitoring/metrics/index.html)
- [Authentication Tokens — CipherTrust Manager 2.18](https://thalesdocs.com/ctp/cm/2.18/admin/cm_admin/authentication/tokens/index.html)

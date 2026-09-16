# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A load generator and benchmark kit for Thales CipherTrust RESTful Data Protection (CRDP) running on Kubernetes. It sends
PROTECT then REVEAL traffic through CRDP's bulk REST API, measures throughput and latency, and correlates that with backend
CPU and CPU steal to work out whether the client, the ingress, or the CRDP backend is the bottleneck. README.md is the
user-facing reference for every CLI flag; Monitoring/README.md is the observability install guide.

There is no test suite, linter, or build step. Development is on Windows (`py`), but the scripts run on Linux load hosts
and cluster nodes (`python3`, bash).

## Commands

```bash
pip install -r CRDP_Stress_App/requirements.txt   # requests, tqdm, termcolor, psutil, orjson

# Run from CRDP_Stress_App/ (multi_client.py resolves CRDP_Stress.py next to itself; test inputs are in ../Test_Data/)
python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy MyPolicy -user alice -iterations 1000 -batchsize 100 -threads 10 -jsonout run.json
python3 multi_client.py -clients 6 -endpoint $CRDP_HOST -policy CRDP_Digitsonly_Protection -user alice -iterations 2000000 -batchsize 5000 -threads 20

# Deploy / tear down CRDP (run from CRDP_K8_Deployment/; needs envsubst; add --microk8s or --fqdn <name> as required)
./makeSecretandDeploy.sh
./deleteAndVerifyCRDP-K8.sh
```

Every change needs a live CRDP endpoint to verify end to end. Without one, `py -m py_compile <file>` and `-h` are the only
local checks.

## Architecture

**The stress app (`CRDP_Stress_App/`).** `CRDP_Stress.py` is a top-level script (no `main()`): it parses args, builds a flat
plaintext array (random string, base64 of a `-payload` file, or CSV cells repeated `-iterations` times), slices it into
bulk messages of `-batchsize`, then runs PROTECT and REVEAL phases. Each phase takes one of two paths:

- `-threads 1`: a sequential loop over `CRDP_REST_API.protectBulkData` / `revealBulkData`.
- `-threads > 1`: `parallel_execution.execute_{protect,reveal}_messages_parallel`, which deals messages round-robin to a
  `ThreadPoolExecutor`, where each worker holds its own `requests.Session`, and reorders results by message index.

Both paths produce an `AggregatedMetrics` (the sequential path through `single_worker_aggregate`), so the on-screen summary
and the `-jsonout` record (`build_phase_record`) come out the same either way.

**The REST client exists twice.** `CRDP_REST_API.py` has the plain `requests.post` versions (used by the sequential path and
the CSV column pre-screen). `parallel_execution.py` has `*_session` copies (used by the threaded path). A change to request
or response handling (payload fields, `external_version`, error handling) must go into both. Both use the `_dumps`/`_loads`
helpers from `CRDP_REST_API.py`, which switch to orjson when it's installed. orjson releases the GIL, so it matters for
client-side throughput.

The plain helpers call `exit()` on HTTP or network errors; the `*_session` copies raise, and the worker catches the
exception, prints it, and stops that worker. Bulk PROTECT returns entries that each carry their own `external_version`
(when the policy uses one). Those entries go back to bulk REVEAL unchanged, so no version value is passed around separately.

**The `-jsonout` schema is a contract.** `multi_client.py` (`aggregate_phase`), `benchmark/spread_launcher.py` (imports
`multi_client` through a `sys.path` insert) and `benchmark/aggregate_profile.py` all read the per-phase fields, notably
`txns_per_sec`, `total_txns`, `wall_start_epoch`, `wall_end_epoch`, `wall_time_sec` and `client_cpu`. Renaming or removing a
field breaks those tools.

**Processes rather than threads for scale.** Throughput levels off at about 20 threads per process because of the GIL.
`multi_client.py` (one host, one endpoint) and `benchmark/spread_launcher.py` (round-robins children across several node
NodePort endpoints) launch independent `CRDP_Stress.py` processes, each with its own `-jsonout`/`-label`, and then combine the
results. Client CPU from psutil covers the whole host, so co-located children all report the same figure.

**Benchmark pipeline (`benchmark/`).** Load run (`spread_launcher.py`) → backend samples, either `prom_snapshot.py`
(preferred; rebuilds the data from Prometheus after the run) or the in-band `sample_backend.sh` / `sample_steal.sh` over SSH.
Both write the same `backend.jsonl` / `steal_<node>.jsonl` → `aggregate_profile.py` (per-profile `agg_<profile>.json`,
including the "clean-node corrected" efficiency that removes steal bias) → `sizing.py` → `make_charts.py` (optional
matplotlib) → `gen_report.py` (Markdown) → `build_report.sh` (pandoc to DOCX). Output goes under `results/` (gitignored).

**Kubernetes.** `crdp-app-svc-ing.yml` and `crdp-ingress.yml` contain `${KEY_MANAGER_HOST}` / `${CRDP_HOST}` placeholders
filled in by `envsubst` at apply time, so don't apply them directly with kubectl. The replica count and CPU request are sized
together (see the SIZING comment in the file). NodePort is 32085. Monitoring targets RKE2 (`Monitoring/rke2/`); accurate
cAdvisor rates for short runs depend on the kubelet's `--housekeeping-interval=5s`.

## Conventions and gotchas

- `.gitattributes` forces LF on `*.sh`, because the scripts run on Linux hosts. Keep it that way when editing from Windows.
- Never commit secrets: `REG_TOKEN_VALUE` stays in the environment, and `Monitoring/prometheus/scrape_configs.yml` is a
  template with a `__K8S_TOKEN__` placeholder (the rendered file and `*.token` are gitignored).
- Runs write `*_protected.*` files next to their inputs; these are gitignored artifacts.
- Code style follows what's already there: `t_`-prefixed parameters and `colored(...)` console output in the app. Benchmark
  scripts are standalone argparse CLIs whose header comment describes their inputs and outputs.

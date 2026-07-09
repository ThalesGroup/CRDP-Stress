# prom_snapshot.py -- pull a benchmark run's backend metrics out of Prometheus.
#
# Drop-in replacement for the in-band shell samplers (sample_steal.sh,
# sample_backend.sh). Those had to run *during* the load and write JSONL locally;
# Prometheus already scraped everything, so we can reconstruct the same files
# after the fact, from any host.
#
# Emits, into --out, exactly what aggregate_profile.py already reads:
#   steal_<node>.jsonl   {"epoch": float, "busy_pct": float, "steal_pct": float}
#   backend.jsonl        {"epoch": float, "total_m": float, "per_node_m": {...}}
#
# backend.jsonl is the improvement. aggregate_profile.py currently ignores it
# (metrics-server lags 15-25s, so `kubectl top` was unusable for short runs) and
# instead *estimates* backend cores as (busy% - idle_baseline%) x 16 vCPU. Here
# per_node_m is measured directly from cAdvisor's counter -- only CRDP's cgroups,
# nothing else on the node.
#
# Usage -- pass the PHASE window, straight from the client JSONs' wall_start_epoch
# and wall_end_epoch. Do NOT pre-pad it; --pad handles that (see below).
#
#   py benchmark/prom_snapshot.py --start 1783622612 --end 1783622653 \
#       --rate-window 30s --out results/digits/
#
# Two windowing subtleties, both learned the hard way:
#
#  * `--pad` (default 60s) widens only the EMITTED jsonl, not the summary. The
#    aggregator derives each node's idle baseline as min(busy_pct) across the
#    whole file (aggregate_profile.py:90), so the file must contain genuinely
#    idle samples on either side of the load or that baseline collapses to the
#    loaded value and the estimated core count goes to ~zero.
#
#  * The printed summary skips the first `--rate-window` seconds of the phase.
#    rate() at time t looks BACKWARD, so the earliest in-phase samples average in
#    pre-load idle and drag the mean down. Trimming a fixed fraction of rows (a
#    20/80 trim) does not fix this -- the correct trim is exactly one rate window.
#
# cAdvisor resolution: the kubelet must run with --housekeeping-interval=5s
# (Monitoring/rke2/apply_rke2_metrics.sh). At the 10s default, cAdvisor advances
# its counter only every ~12s, and rate(...[15s]) silently returns data for a
# handful of pods instead of all 24. Scrape interval alone cannot fix this --
# cAdvisor stamps its own timestamps.
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PROM = "http://192.168.1.186:9090"
CLUSTER_NODES = ["kube", "sphere", "cone"]
NODE_VCPU = 16

# container!="" drops cAdvisor's pod-level cgroup rollup, which would otherwise
# double-count every pod alongside its own container.
Q_CORES = ('sum by (node) (rate(container_cpu_usage_seconds_total'
           '{{job="cadvisor", pod=~"{pod_re}", container!=""}}[{w}]))')
Q_PODS = ('count by (node) (rate(container_cpu_usage_seconds_total'
          '{{job="cadvisor", pod=~"{pod_re}", container!=""}}[{w}]) > 0)')

# job="node-exporter" is REQUIRED, not decorative: the CipherTrust Manager
# (job="CM-Kirk") embeds its own node exporter and publishes 32 series of
# node_cpu_seconds_total for its own host. Those carry no `node` label, so
# without this filter they fold into the results as a phantom entry and skew
# any cluster-wide aggregation.
Q_BUSY = ('100 - avg by (node) (rate(node_cpu_seconds_total'
          '{{job="node-exporter", mode="idle"}}[{w}])) * 100')
Q_STEAL = ('avg by (node) (rate(node_cpu_seconds_total'
           '{{job="node-exporter", mode="steal"}}[{w}])) * 100')

_DUR = {"s": 1, "m": 60, "h": 3600}


def dur_seconds(s):
    m = re.fullmatch(r"(\d+)([smh])", s)
    if not m:
        raise SystemExit("bad duration %r (want e.g. 30s, 1m)" % s)
    return int(m.group(1)) * _DUR[m.group(2)]


def query_range(prom, query, start, end, step):
    """Return {node_label: {epoch: value}} for a range query."""
    url = prom.rstrip("/") + "/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": query, "start": start, "end": end, "step": step})
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit("Prometheus HTTP %s for query %r\n%s"
                         % (e.code, query, e.read().decode("utf-8", "replace")[:400]))
    except urllib.error.URLError as e:
        raise SystemExit("cannot reach Prometheus at %s: %s" % (prom, e.reason))
    if payload.get("status") != "success":
        raise SystemExit("Prometheus error: %s" % payload.get("error", payload))
    return {s["metric"].get("node", "<unlabelled>"):
            {float(ts): float(v) for ts, v in s["values"]}
            for s in payload["data"]["result"]}


def write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return len(rows)


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def main():
    ap = argparse.ArgumentParser(
        description="Rebuild a benchmark run's backend JSONL from Prometheus.")
    ap.add_argument("--start", type=float, required=True,
                    help="phase wall_start_epoch (unix seconds)")
    ap.add_argument("--end", type=float, required=True,
                    help="phase wall_end_epoch (unix seconds)")
    ap.add_argument("--out", required=True, help="run directory to write into")
    ap.add_argument("--prom", default=DEFAULT_PROM)
    ap.add_argument("--step", type=int, default=5, help="sample resolution, seconds")
    ap.add_argument("--rate-window", default="30s",
                    help="rate() window. Must span >=2 cAdvisor samples; with "
                         "housekeeping-interval=5s, 30s is safe and 15s is marginal.")
    ap.add_argument("--pad", type=float, default=60,
                    help="idle seconds to include on each side of the emitted "
                         "jsonl so aggregate_profile.py can find an idle baseline")
    ap.add_argument("--pod-regex", default="crdp-deployment-.*")
    ap.add_argument("--nodes", default=",".join(CLUSTER_NODES),
                    help="cluster nodes only -- cm-neptune is the load client, not a backend")
    ap.add_argument("--tps", type=float, default=0,
                    help="phase txns/sec; if given, prints measured efficiency")
    args = ap.parse_args()

    if args.end <= args.start:
        raise SystemExit("--end must be after --start")
    nodes = [n.strip() for n in args.nodes.split(",") if n.strip()]
    warmup = dur_seconds(args.rate_window)
    if args.end - args.start <= warmup:
        print("WARNING: the phase (%.0fs) is not longer than --rate-window (%ds).\n"
              "         There is no uncontaminated steady window; shorten the rate\n"
              "         window or run a longer load."
              % (args.end - args.start, warmup), file=sys.stderr)

    os.makedirs(args.out, exist_ok=True)
    w = args.rate_window
    f0, f1 = args.start - args.pad, args.end + args.pad   # emitted (padded) range

    busy = query_range(args.prom, Q_BUSY.format(w=w), f0, f1, args.step)
    steal = query_range(args.prom, Q_STEAL.format(w=w), f0, f1, args.step)
    cores = query_range(args.prom, Q_CORES.format(pod_re=args.pod_regex, w=w), f0, f1, args.step)
    pods = query_range(args.prom, Q_PODS.format(pod_re=args.pod_regex, w=w), args.start + warmup, args.end, args.step)

    missing = [n for n in nodes if n not in busy]
    if missing:
        raise SystemExit(
            "no node_exporter data for %s in [%.0f, %.0f].\n"
            "Is the DaemonSet running, and is the `node` label set in the scrape config?"
            % (", ".join(missing), f0, f1))

    # --- steal_<node>.jsonl: what sample_steal.sh used to produce -------------
    for node in nodes:
        rows = [{"epoch": ts,
                 "busy_pct": round(busy[node][ts], 2),
                 "steal_pct": round(steal.get(node, {}).get(ts, 0.0), 2)}
                for ts in sorted(busy[node])]
        n = write_jsonl(os.path.join(args.out, "steal_%s.jsonl" % node), rows)
        print("  steal_%-14s %4d samples" % (node + ".jsonl", n))

    # --- backend.jsonl: measured per-pod CPU, in millicores -------------------
    if not cores:
        print("\nWARNING: no CRDP pod CPU matched %r. Either no load ran in this\n"
              "window, or the pod regex is wrong. backend.jsonl not written."
              % args.pod_regex, file=sys.stderr)
        return

    stamps = sorted(set().union(*(set(v) for v in cores.values())))
    rows = []
    for ts in stamps:
        per_node = {n: round(cores.get(n, {}).get(ts, 0.0) * 1000, 1) for n in nodes}
        rows.append({"epoch": ts, "total_m": round(sum(per_node.values()), 1),
                     "per_node_m": per_node})
    print("  backend.jsonl          %4d samples" % write_jsonl(
        os.path.join(args.out, "backend.jsonl"), rows))

    # --- steady-window summary ----------------------------------------------
    # Skip exactly one rate window: rate() looks backward, so samples taken less
    # than one window after the phase started still average in pre-load idle.
    s0 = args.start + warmup
    steady = [ts for ts in stamps if s0 <= ts <= args.end]
    if len(steady) < 2:
        print("\nsteady window has %d samples -- too few to summarize." % len(steady),
              file=sys.stderr)
        return

    # The idle baseline comes from the padded head, mirroring aggregate_profile.py.
    base = {n: min((busy[n][ts] for ts in busy[n] if ts < args.start - warmup),
                   default=0.0) for n in nodes}

    print("\nPROTECT/REVEAL steady window: %.0fs (first %ds skipped: one rate window)\n"
          % (args.end - s0, warmup))
    print("%-8s %10s %9s %8s %13s %10s" % ("node", "cAdvisor", "busy%", "steal%",
                                           "proc-est", "pods seen"))
    print("-" * 66)
    tot_cad = tot_est = 0.0
    for n in nodes:
        cad = mean(cores.get(n, {}).get(ts, 0.0) for ts in steady)
        b = mean(busy[n][ts] for ts in steady if ts in busy[n])
        st = mean(steal.get(n, {}).get(ts, 0.0) for ts in steady)
        est = max(b - base[n], 0.0) / 100.0 * NODE_VCPU
        npod = mean(pods.get(n, {}).values())
        tot_cad += cad
        tot_est += est
        print("%-8s %10.1f %8.1f%% %7.2f%% %13.1f %10.0f" % (n, cad, b, st, est, npod))
    print("-" * 66)
    print("%-8s %10.1f %9s %8s %13.1f" % ("TOTAL", tot_cad, "", "", tot_est))

    if args.tps:
        print("\nefficiency, cAdvisor : %7.0f txns/sec/core" % (args.tps / tot_cad))
        print("efficiency, proc-est : %7.0f txns/sec/core" % (args.tps / tot_est))

    # Empirically these agree within a few percent (measured 44.3 vs 42.7 on an
    # 8-client digits PROTECT run, against 43.5 in results/report.md). They are
    # not identical and neither strictly bounds the other: the estimate sweeps in
    # kubelet/containerd/canal CPU *and* counts steal as busy, which inflates it,
    # while cAdvisor measures only CRDP's cgroups. Treat a gap under ~10% as
    # agreement; a larger one means the core-attribution method needs review.
    skew = tot_cad / tot_est if tot_est else float("nan")
    verdict = "agree" if 0.9 <= skew <= 1.1 else "DISAGREE -- investigate"
    print("\ncAdvisor / proc-estimate = %.2f  (%s)" % (skew, verdict))


if __name__ == "__main__":
    main()

# aggregate_profile.py — build one per-profile summary JSON from a benchmark run.
#
# Inputs (a run directory produced by spread_launcher.py plus samplers):
#   <run>/client_*.json                 per-child CRDP_Stress results (protect+reveal)
#   <run>/backend.jsonl   (optional)    per-node CRDP pod CPU + node CPU (sample_backend.sh)
#   <run>/steal_<node>.jsonl (optional) per-node /proc/stat steal% (sample_steal.sh)
#   <run>/podcounts.json  (optional)    {"kube":8,"sphere":8,"cone":8}
#
# Output: agg_<profile>.json with, per phase (protect/reveal): client throughput
# (sum-of-rates + overlapped-window), pooled latency, backend cores-used per node,
# per-node steal%, and raw + clean-node-corrected efficiency (txns/sec/core).
#
# Clean-node correction: pods are homogeneous and the ClusterIP service round-robins
# txns across all pods, so txns served by the low-steal nodes (kube+sphere) ≈
# total_txns * clean_pods/total_pods, and their cores = sum of their pod CPU. This
# removes the cm-neptune->cone steal bias from the efficiency used for sizing.
import argparse
import glob
import json
import os
import statistics


def load_json(path):
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    out = []
    if not path or not os.path.isfile(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def steady_window(starts, ends, trim):
    w0, w1 = min(starts), max(ends)
    span = w1 - w0
    return w0 + trim * span, w1 - trim * span, w0, w1


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def phase_summary(results, phase, backend, steals, podcounts, trim, clean_nodes):
    ph = [r[phase] for r in results if phase in r and r[phase].get("total_txns")]
    if not ph:
        return None

    starts = [p["wall_start_epoch"] for p in ph if p.get("wall_start_epoch")]
    ends = [p["wall_end_epoch"] for p in ph if p.get("wall_end_epoch")]
    sw0, sw1, w0, w1 = steady_window(starts, ends, trim)
    window = max(w1 - w0, 1e-9)

    per_client = [p["txns_per_sec"] for p in ph]
    total_txns = sum(p["total_txns"] for p in ph)
    sum_of_rates = sum(per_client)
    overlapped_rate = total_txns / window

    # Pooled latency = mean of per-client percentiles (documented approximation).
    lat = {k: mean([p["latency_ms"][k] for p in ph if "latency_ms" in p])
           for k in ("p50", "p95", "p99", "max")}
    host_cpu_peak = max((p["client_cpu"].get("peak", 0) for p in ph
                         if p.get("client_cpu", {}).get("available")), default=None)
    host_cores = next((p["client_cpu"].get("cores") for p in ph
                       if p.get("client_cpu", {}).get("available")), None)

    # Merge per-client rolling (1-sec-bucketed) series by summing element-wise.
    rolls = [p.get("rolling_txns_per_sec", []) for p in ph]
    max_len = max((len(r) for r in rolls), default=0)
    rolling = [round(sum(r[i] for r in rolls if i < len(r))) for i in range(max_len)]

    # ---- Backend cores-used over the steady window ----
    bsamp = [s for s in backend if sw0 <= s.get("epoch", 0) <= sw1] or backend
    total_cores = mean([s["pod"]["total_m"] for s in bsamp if "pod" in s]) / 1000.0
    per_node_cores = {}
    node_keys = set()
    for s in bsamp:
        for k in s.get("pod", {}).get("per_node_m", {}):
            node_keys.add(k)
    for k in node_keys:
        per_node_cores[k] = mean([s["pod"]["per_node_m"].get(k, 0) for s in bsamp]) / 1000.0

    # ---- Steal% per node over the steady window ----
    steal_pct, busy_pct = {}, {}
    for node, samp in steals.items():
        w = [x for x in samp if sw0 <= x.get("epoch", 0) <= sw1] or samp
        steal_pct[node] = round(mean([x["steal_pct"] for x in w]), 1)
        busy_pct[node] = round(mean([x["busy_pct"] for x in w]), 1)

    # ---- Efficiency ----
    total_pods = sum(podcounts.values()) if podcounts else None
    clean_pods = sum(podcounts.get(n, 0) for n in clean_nodes) if podcounts else None
    clean_cores = sum(per_node_cores.get(n, 0) for n in clean_nodes)

    raw_eff = (overlapped_rate / total_cores) if total_cores > 0 else None
    clean_eff = None
    if total_pods and clean_pods and clean_cores > 0:
        clean_eff = (overlapped_rate * clean_pods / total_pods) / clean_cores

    return {
        "phase": phase.upper(),
        "client": {
            "n_clients": len(ph),
            "sum_of_rates_tps": round(sum_of_rates),
            "overlapped_window_tps": round(overlapped_rate),
            "total_txns": total_txns,
            "window_sec": round(window, 1),
            "per_client_tps": {"min": round(min(per_client)), "mean": round(mean(per_client)),
                               "max": round(max(per_client))},
            "latency_ms": {k: round(v, 1) for k, v in lat.items()},
            "host_cpu_peak_pct": host_cpu_peak,
            "host_cores": host_cores,
            "mb_per_sec": round(mean([p.get("mb_per_sec", 0) for p in ph]), 3),
            "rolling_tps": rolling,
        },
        "backend": {
            "steady_window": [round(sw0, 1), round(sw1, 1)],
            "samples_used": len(bsamp),
            "total_cores_used": round(total_cores, 1),
            "per_node_cores_used": {k: round(v, 1) for k, v in per_node_cores.items()},
            "node_steal_pct": steal_pct,
            "node_busy_pct": busy_pct,
            "pod_counts": podcounts or {},
        },
        "efficiency_tps_per_core": {
            "raw": round(raw_eff) if raw_eff else None,
            "clean_node_corrected": round(clean_eff) if clean_eff else None,
            "clean_nodes": list(clean_nodes),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--profile", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--charset", default="")
    ap.add_argument("--payload-bytes", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--trim", type=float, default=0.2, help="Fraction trimmed from each end for steady window.")
    ap.add_argument("--clean-nodes", default="kube,sphere")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    results = [load_json(p) for p in sorted(glob.glob(os.path.join(args.run_dir, "client_*.json")))]
    if not results:
        raise SystemExit("no client_*.json in %s" % args.run_dir)

    backend = load_jsonl(os.path.join(args.run_dir, "backend.jsonl"))
    steals = {}
    for p in glob.glob(os.path.join(args.run_dir, "steal_*.jsonl")):
        node = os.path.basename(p)[len("steal_"):-len(".jsonl")]
        steals[node] = load_jsonl(p)
    pc_path = os.path.join(args.run_dir, "podcounts.json")
    podcounts = load_json(pc_path) if os.path.isfile(pc_path) else {}
    clean_nodes = [n.strip() for n in args.clean_nodes.split(",") if n.strip()]

    out = {
        "profile": args.profile,
        "policy": args.policy,
        "charset": args.charset,
        "payload_bytes": args.payload_bytes,
        "run_dir": os.path.abspath(args.run_dir),
        "note": args.note,
        "protect": phase_summary(results, "protect", backend, steals, podcounts, args.trim, clean_nodes),
        "reveal": phase_summary(results, "reveal", backend, steals, podcounts, args.trim, clean_nodes),
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print("wrote %s" % args.out)
    for ph in ("protect", "reveal"):
        s = out[ph]
        if s:
            e = s["efficiency_tps_per_core"]
            print("  %-7s overlapped=%s tps  cores=%s  eff raw=%s clean=%s tps/core"
                  % (ph, s["client"]["overlapped_window_tps"], s["backend"]["total_cores_used"],
                     e["raw"], e["clean_node_corrected"]))


if __name__ == "__main__":
    main()

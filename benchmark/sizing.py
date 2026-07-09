# sizing.py — compute the ≥1M txns/sec hardware recommendation from measured efficiency.
#
# Input: the per-profile agg_<profile>.json files (from aggregate_profile.py).
# Output: sizing.json — per-profile required dedicated physical cores for the target,
# the combined cluster (sized to the least-efficient PROTECT profile, since each
# profile must independently clear the target), supporting infra, and load-generation
# sizing (how many client cores/hosts are needed to *drive* the target).
import argparse
import json
import math


def eff_of(phase_rec):
    """Prefer the clean-node-corrected efficiency; fall back to raw."""
    if not phase_rec:
        return None
    e = phase_rec["efficiency_tps_per_core"]
    return e.get("clean_node_corrected") or e.get("raw")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("agg", nargs="+", help="agg_<profile>.json files")
    ap.add_argument("--target", type=float, default=1_000_000)
    ap.add_argument("--cores-per-node", type=int, default=16, help="Physical cores per dedicated worker node.")
    ap.add_argument("--headroom", type=float, default=1.20, help="Safety factor over the bare minimum.")
    ap.add_argument("--ram-per-node-gb", type=int, default=16)
    ap.add_argument("--loadhost-cores", type=int, default=16, help="Cores per load-generation host.")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    profiles = [json.load(open(p)) for p in args.agg]

    per_profile = []
    for pr in profiles:
        ep = eff_of(pr.get("protect"))
        er = eff_of(pr.get("reveal"))
        rec = {
            "profile": pr["profile"], "policy": pr["policy"],
            "protect_eff_tps_per_core": ep,
            "reveal_eff_tps_per_core": er,
            "protect_cores_for_target": math.ceil(args.target / ep) if ep else None,
            "reveal_cores_for_target": math.ceil(args.target / er) if er else None,
            # measured client-side driving efficiency (txns/sec per load-host logical core)
            "protect_measured_tps": (pr.get("protect") or {}).get("client", {}).get("overlapped_window_tps"),
            "reveal_measured_tps": (pr.get("reveal") or {}).get("client", {}).get("overlapped_window_tps"),
        }
        # load-gen driving rate from PROTECT run (sum-of-rates / host cores)
        cpr = (pr.get("protect") or {}).get("client", {})
        if cpr.get("host_cores"):
            rec["protect_tps_per_client_core"] = round(cpr["sum_of_rates_tps"] / cpr["host_cores"])
        per_profile.append(rec)

    # Gating = the profile needing the MOST cores for PROTECT (each must clear target).
    gating = max((p for p in per_profile if p["protect_cores_for_target"]),
                 key=lambda p: p["protect_cores_for_target"], default=None)
    backend_cores_min = gating["protect_cores_for_target"] if gating else None
    backend_cores_rec = math.ceil(backend_cores_min * args.headroom) if backend_cores_min else None
    nodes_rec = math.ceil(backend_cores_rec / args.cores_per_node) if backend_cores_rec else None

    # Load-gen sizing: hardest to DRIVE = highest txns/sec profile (most client work).
    drive = [p for p in per_profile if p.get("protect_tps_per_client_core")]
    loadgen = None
    if drive:
        worst = min(drive, key=lambda p: p["protect_tps_per_client_core"])  # lowest per-core drive = most hosts
        best = max(drive, key=lambda p: p.get("protect_measured_tps") or 0)  # highest throughput profile
        client_cores = math.ceil(args.target / best["protect_tps_per_client_core"])
        loadgen = {
            "reference_profile": best["profile"],
            "tps_per_client_core": best["protect_tps_per_client_core"],
            "client_cores_for_target": client_cores,
            "load_hosts": math.ceil(client_cores / args.loadhost_cores),
            "loadhost_cores_each": args.loadhost_cores,
            "note": "Load hosts must NOT share a hypervisor with CRDP worker nodes.",
        }

    out = {
        "target_tps": args.target,
        "assumptions": {
            "cores_per_node": args.cores_per_node,
            "headroom": args.headroom,
            "ram_per_node_gb": args.ram_per_node_gb,
            "basis": "dedicated physical cores, 1 vCPU : 1 physical core, no over-commit",
        },
        "per_profile": per_profile,
        "gating_profile": gating["profile"] if gating else None,
        "recommendation": {
            "backend_cores_min": backend_cores_min,
            "backend_cores_recommended": backend_cores_rec,
            "worker_nodes": nodes_rec,
            "cores_per_node": args.cores_per_node,
            "ram_per_node_gb": args.ram_per_node_gb,
            "supporting_infra": {
                "key_manager_cores": 8,
                "control_plane_cores": 6,
                "note": "Key manager, control-plane/etcd, and load hosts each on their OWN "
                        "dedicated (non-oversubscribed) hypervisor; do not co-locate with CRDP workers.",
            },
            "load_generation": loadgen,
        },
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    print("wrote %s" % args.out)
    print("  gating profile: %s -> %s cores min (%s w/ headroom) -> %s x %s-core nodes"
          % (out["gating_profile"], backend_cores_min, backend_cores_rec, nodes_rec, args.cores_per_node))


if __name__ == "__main__":
    main()

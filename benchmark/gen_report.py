# gen_report.py — assemble the Markdown technical report from the aggregated data.
# Inputs: agg_<profile>.json (per profile), sizing.json, versions.json, chart PNGs.
# Output: report.md (exec summary + technical body + appendix). No external deps
# (tables rendered by a tiny hand-rolled pipe-table formatter).
import argparse
import glob
import json
import os

ORDER = ["digits", "alphanumeric", "binary"]  # preferred display order


def md_table(headers, rows):
    def cell(x):
        return "" if x is None else str(x)
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(cell(c) for c in r) + " |")
    return "\n".join(out)


def kf(x):
    return f"{x:,.0f}" if isinstance(x, (int, float)) else (x or "—")


def img(charts_dir, name, alt):
    p = os.path.join(charts_dir, name)
    # Always emit a forward-slash relative path so pandoc resolves it on any OS.
    rel = os.path.basename(charts_dir) + "/" + name
    return f"![{alt}]({rel})\n" if os.path.isfile(p) else ""


def order_key(pr):
    prof = pr.get("profile", "")
    return (ORDER.index(prof) if prof in ORDER else len(ORDER), prof)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agg-glob", required=True)
    ap.add_argument("--sizing", required=True)
    ap.add_argument("--versions", required=True)
    ap.add_argument("--charts-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", type=float, default=1_000_000)
    ap.add_argument("--date", default="")
    args = ap.parse_args()

    profiles = sorted((json.load(open(p)) for p in glob.glob(args.agg_glob)), key=order_key)
    sizing = json.load(open(args.sizing))
    versions = json.load(open(args.versions))
    cd = args.charts_dir
    T = args.target
    L = []  # lines

    gating = sizing.get("gating_profile")
    rec = sizing.get("recommendation", {})

    # ---------------- Title ----------------
    L.append("# CRDP Throughput Benchmark & Scaling Report\n")
    L.append(f"**Target:** {T/1e6:.0f},000,000 txns/sec per protection profile (PROTECT gates) "
             f"· **Date:** {args.date or versions.get('date','')}\n")

    # ---------------- Executive Summary ----------------
    L.append("## Executive Summary\n")
    L.append("This report measures the throughput of the current CipherTrust RESTful Data Protection "
             "(CRDP) deployment across three protection profiles, then recommends a configuration that "
             "delivers at least **1,000,000 transactions/sec for each profile independently**.\n")
    rows = []
    for pr in profiles:
        prot = (pr.get("protect") or {}).get("client", {})
        rev = (pr.get("reveal") or {}).get("client", {})
        rows.append([pr["profile"], pr["policy"],
                     kf(prot.get("overlapped_window_tps")), kf(rev.get("overlapped_window_tps"))])
    L.append("**Current-deployment throughput (as measured):**\n")
    L.append(md_table(["Profile", "Policy", "PROTECT txns/sec", "REVEAL txns/sec"], rows) + "\n")
    L.append(img(cd, "throughput.png", "Throughput by profile"))
    if rec.get("worker_nodes"):
        L.append(f"\n**Recommendation to reach the target:** the least-efficient profile "
                 f"(**{gating}**) gates the design. Sizing to it on **dedicated physical cores** requires "
                 f"~**{rec.get('backend_cores_recommended')} CRDP cores** — approximately "
                 f"**{rec['worker_nodes']} × {rec['cores_per_node']}-core worker nodes**, each on its own "
                 f"non-oversubscribed hypervisor, plus dedicated hosts for the key manager, control plane, "
                 f"and load generators. The cheaper profiles then clear the target on the same footprint.\n")
    L.append("> **Key caveat:** the cluster nodes are virtual machines over-committed across shared "
             "hypervisors, so a vCPU is not a physical core (CPU *steal*). All efficiency and sizing "
             "figures below are stated on a **dedicated physical-core** basis.\n")

    # ---------------- Environment & Methodology ----------------
    L.append("## Environment & Methodology\n")
    L.append("### Versions\n")
    vrows = []
    for host, v in versions.get("hosts", {}).items():
        vrows.append([host, v.get("os", ""), v.get("kernel", ""), v.get("cpu", ""), v.get("ram", "")])
    if vrows:
        L.append(md_table(["Host", "OS", "Kernel", "vCPU", "RAM"], vrows) + "\n")
    L.append(md_table(["Component", "Version"], [
        ["Kubernetes / RKE2", versions.get("kubernetes", "")],
        ["Container runtime", versions.get("container_runtime", "")],
        ["CRDP image", versions.get("crdp_image", "")],
        ["CRDP image digest", versions.get("crdp_digest", "")],
        ["Key manager", versions.get("key_manager", "")],
        ["CRDP service TLS", versions.get("server_mode", "")],
    ]) + "\n")
    L.append("### Cluster & hypervisor layout\n")
    L.append(versions.get("layout_note", "") + "\n")
    L.append("### Method\n")
    L.append("- **Load** generated entirely from the Linux host **cm-neptune**, spread round-robin across "
             "the three node NodePorts (`:32085`). Per-core efficiency (txns/sec ÷ backend cores used) is the "
             "sizing input.\n"
             "- **Backend sampling:** CRDP pod CPU (per node) and node CPU sampled every ~2 s; only the "
             "steady middle of each run is used.\n"
             "- **CPU steal measured directly** on each node via `/proc/stat` — the physical-vs-vCPU truth.\n"
             "- **Steal correction:** cm-neptune shares a hypervisor with node `cone`, so it steals CPU from "
             "cone's pods. Because the service round-robins txns across homogeneous pods, efficiency is also "
             "computed from the low-steal nodes (`kube`+`sphere`) — the **clean-node-corrected** figure used "
             "for sizing.\n"
             "- **Payload note:** the alphanumeric profile protects a 64-char field vs 19 chars for digits — "
             "a documented per-profile difference, not an error.\n")

    # ---------------- Results ----------------
    L.append("## Current-Deployment Results\n")
    for pr in profiles:
        L.append(f"### {pr['profile']} — `{pr['policy']}`\n")
        rows = []
        for phase in ("protect", "reveal"):
            s = pr.get(phase)
            if not s:
                continue
            c = s["client"]; b = s["backend"]; lat = c["latency_ms"]
            rows.append([phase.upper(), kf(c["overlapped_window_tps"]), kf(c["sum_of_rates_tps"]),
                         c["mb_per_sec"], f"{lat['p50']}/{lat['p95']}/{lat['p99']}",
                         b["total_cores_used"], kf((s['efficiency_tps_per_core'] or {}).get('clean_node_corrected'))])
        L.append(md_table(["Phase", "txns/sec (window)", "txns/sec (sum)", "MB/s",
                           "lat p50/p95/p99 ms", "backend cores", "eff (clean) tps/core"], rows) + "\n")
    L.append(img(cd, "rolling.png", "Rolling throughput"))

    # ---------------- Analysis ----------------
    L.append("## Analysis\n")
    L.append("### Per-core efficiency\n")
    erows = []
    for pr in profiles:
        e = (pr.get("protect") or {}).get("efficiency_tps_per_core", {})
        erows.append([pr["profile"], kf(e.get("raw")), kf(e.get("clean_node_corrected"))])
    L.append(md_table(["Profile", "raw tps/core (whole cluster)", "clean-node-corrected tps/core"], erows) + "\n")
    L.append(img(cd, "efficiency.png", "Per-core efficiency"))
    L.append("### Oversubscription evidence (CPU steal)\n")
    srows = []
    for pr in profiles:
        st = (pr.get("protect") or {}).get("backend", {}).get("node_steal_pct", {})
        srows.append([pr["profile"], st.get("kube", "—"), st.get("sphere", "—"), st.get("cone", "—")])
    L.append(md_table(["Profile (PROTECT)", "kube steal%", "sphere steal%", "cone steal%"], srows) + "\n")
    L.append(img(cd, "steal.png", "CPU steal by node"))

    # ---------------- Recommendation ----------------
    L.append("## Recommendation — reaching ≥1,000,000 txns/sec per profile\n")
    rows = []
    for p in sizing.get("per_profile", []):
        rows.append([p["profile"], kf(p.get("protect_eff_tps_per_core")),
                     kf(p.get("protect_cores_for_target")), kf(p.get("reveal_cores_for_target"))])
    L.append(md_table(["Profile", "PROTECT eff tps/core", "cores for 1M (PROTECT)", "cores for 1M (REVEAL)"], rows) + "\n")
    L.append(img(cd, "sizing.png", "Cores required by profile"))
    if rec:
        L.append("### Recommended cluster (sized to the gating profile)\n")
        L.append(md_table(["Item", "Value"], [
            ["Gating profile", gating],
            ["Backend cores (minimum)", kf(rec.get("backend_cores_min"))],
            [f"Backend cores (recommended, {sizing['assumptions']['headroom']}× headroom)", kf(rec.get("backend_cores_recommended"))],
            ["Worker nodes", f"{rec.get('worker_nodes')} × {rec.get('cores_per_node')} dedicated physical cores"],
            ["RAM per node", f"{rec.get('ram_per_node_gb')} GB"],
            ["Basis", sizing["assumptions"]["basis"]],
        ]) + "\n")
        si = rec.get("supporting_infra", {})
        L.append(f"**Supporting infrastructure (separate, dedicated hypervisors):** key manager "
                 f"~{si.get('key_manager_cores')} cores, control-plane/etcd ~{si.get('control_plane_cores')} "
                 f"cores (tainted). {si.get('note','')}\n")
        lg = rec.get("load_generation")
        if lg:
            L.append(f"**Load generation:** driving {T/1e6:.0f}M txns/sec needs ~**{lg['client_cores_for_target']} "
                     f"client cores** (~{lg['load_hosts']} × {lg['loadhost_cores_each']}-core hosts) at the "
                     f"highest-throughput profile ({lg['reference_profile']}, "
                     f"~{kf(lg['tps_per_client_core'])} tps/client-core). {lg['note']}\n")
    L.append("### Configuration knobs\n")
    L.append("- **Pods:** ~1 per 1.5–2 dedicated cores; **set `requests` = `limits`** on CPU to eliminate "
             "noisy-neighbor variance once cores are dedicated.\n"
             "- **Topology spread** (maxSkew 1) to keep pods balanced across nodes.\n"
             "- **Client:** batch ~5,000, ~20 threads/process, `orjson` installed; scale with client processes.\n"
             "- Optionally an **HPA** on CPU once requests/limits are set.\n")

    # ---------------- Appendix ----------------
    L.append("## Appendix\n")
    L.append("### Per-profile run parameters\n")
    arows = []
    for pr in profiles:
        c = (pr.get("protect") or {}).get("client", {})
        arows.append([pr["profile"], pr.get("charset", ""), pr.get("payload_bytes", ""),
                      c.get("n_clients"), c.get("host_cpu_peak_pct")])
    L.append(md_table(["Profile", "charset", "payload bytes", "clients", "load-host CPU peak %"], arows) + "\n")
    L.append("### Notes\n")
    for pr in profiles:
        if pr.get("note"):
            L.append(f"- **{pr['profile']}:** {pr['note']}")
    L.append("")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("wrote %s (%d lines)" % (args.out, len(L)))


if __name__ == "__main__":
    main()

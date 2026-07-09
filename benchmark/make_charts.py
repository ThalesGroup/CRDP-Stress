# make_charts.py — render report charts from the agg_<profile>.json + sizing.json.
# Runs on the reporting host. Requires matplotlib; if it is not importable this
# script prints a notice and exits 0 (the report falls back to tables only).
#
# Produces (into <charts_dir>):
#   throughput.png    per-profile PROTECT/REVEAL overlapped-window tps vs the target line
#   efficiency.png    per-profile per-core efficiency (raw vs clean-corrected) + FPE/AES refs
#   rolling.png       merged rolling txns/sec (PROTECT) per profile
#   steal.png         per-node steal% under load (the oversubscription evidence)
#   sizing.png        required physical cores per profile vs the recommended cluster
import argparse
import glob
import json
import os
import sys

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("matplotlib not installed — skipping charts (report will use tables only).")
    sys.exit(0)

# Brand-neutral, colorblind-safe palette; light background for DOCX.
C = {"protect": "#2f6db3", "reveal": "#e08a1e", "accent": "#0c7383",
     "good": "#1f8a4c", "warn": "#b4790f", "crit": "#bd3f2c", "grid": "#d9e0e7", "ink": "#20272e"}
plt.rcParams.update({"font.size": 11, "axes.edgecolor": C["ink"], "axes.labelcolor": C["ink"],
                     "text.color": C["ink"], "xtick.color": C["ink"], "ytick.color": C["ink"],
                     "figure.facecolor": "white", "axes.facecolor": "white"})


def short(p):
    return p.get("profile", "?")


def fmt_k(x, _=None):
    return f"{x/1000:.0f}k" if x else "0"


def bar_throughput(profiles, target, path):
    labels = [short(p) for p in profiles]
    prot = [(p.get("protect") or {}).get("client", {}).get("overlapped_window_tps", 0) for p in profiles]
    rev = [(p.get("reveal") or {}).get("client", {}).get("overlapped_window_tps", 0) for p in profiles]
    x = range(len(labels)); w = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar([i - w/2 for i in x], prot, w, label="PROTECT", color=C["protect"])
    ax.bar([i + w/2 for i in x], rev, w, label="REVEAL", color=C["reveal"])
    ax.axhline(target, ls="--", lw=1.5, color=C["crit"])
    ax.text(len(labels)-0.5, target, f" {target/1e6:.0f}M target", va="bottom", ha="right", color=C["crit"], fontsize=9)
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("txns/sec (overlapped window)"); ax.yaxis.set_major_formatter(fmt_k)
    ax.set_title("Current-deployment throughput by profile")
    ax.grid(axis="y", color=C["grid"]); ax.set_axisbelow(True); ax.legend(frameon=False)
    for i, v in enumerate(prot): ax.text(i - w/2, v, fmt_k(v), ha="center", va="bottom", fontsize=8)
    for i, v in enumerate(rev): ax.text(i + w/2, v, fmt_k(v), ha="center", va="bottom", fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def bar_efficiency(profiles, path):
    labels = [short(p) for p in profiles]
    raw = [(p.get("protect") or {}).get("efficiency_tps_per_core", {}).get("raw") or 0 for p in profiles]
    clean = [(p.get("protect") or {}).get("efficiency_tps_per_core", {}).get("clean_node_corrected") or 0 for p in profiles]
    x = range(len(labels)); w = 0.38
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar([i - w/2 for i in x], raw, w, label="raw (whole cluster)", color=C["grid"], edgecolor=C["ink"])
    ax.bar([i + w/2 for i in x], clean, w, label="clean-node corrected", color=C["accent"])
    ax.axhline(16000, ls=":", color=C["protect"]); ax.text(0, 16000, " FPE ref ~16k", va="bottom", fontsize=8, color=C["protect"])
    ax.axhline(28000, ls=":", color=C["good"]); ax.text(0, 28000, " AES-CBC ref ~28k", va="bottom", fontsize=8, color=C["good"])
    ax.set_xticks(list(x)); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("PROTECT txns/sec per core"); ax.yaxis.set_major_formatter(fmt_k)
    ax.set_title("Per-core efficiency (PROTECT)")
    ax.grid(axis="y", color=C["grid"]); ax.set_axisbelow(True); ax.legend(frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def line_rolling(profiles, path):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    colors = [C["protect"], C["accent"], C["good"], C["warn"]]
    plotted = False
    for i, p in enumerate(profiles):
        r = (p.get("protect") or {}).get("client", {}).get("rolling_tps", [])
        if r:
            ax.plot(range(len(r)), r, label=short(p), color=colors[i % len(colors)], lw=1.8)
            plotted = True
    if not plotted:
        plt.close(fig); return
    ax.set_xlabel("seconds"); ax.set_ylabel("txns/sec"); ax.yaxis.set_major_formatter(fmt_k)
    ax.set_title("Rolling throughput (PROTECT)")
    ax.grid(color=C["grid"]); ax.set_axisbelow(True); ax.legend(frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def bar_steal(profiles, path):
    # Use the first profile's steal snapshot as representative (all runs similar).
    nodes = ["kube", "sphere", "cone"]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    x = range(len(nodes)); w = 0.8 / max(len(profiles), 1)
    any_data = False
    for i, p in enumerate(profiles):
        st = (p.get("protect") or {}).get("backend", {}).get("node_steal_pct", {})
        vals = [st.get(n, 0) for n in nodes]
        if any(vals):
            any_data = True
        ax.bar([j + i*w for j in x], vals, w, label=short(p))
    if not any_data:
        plt.close(fig); return
    ax.set_xticks([j + w*(len(profiles)-1)/2 for j in x]); ax.set_xticklabels(nodes)
    ax.set_ylabel("CPU steal % (under load)")
    ax.set_title("Hypervisor CPU steal by node — oversubscription evidence")
    ax.grid(axis="y", color=C["grid"]); ax.set_axisbelow(True); ax.legend(frameon=False, fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def bar_sizing(sizing, path):
    pp = sizing.get("per_profile", [])
    labels = [p["profile"] for p in pp]
    cores = [p.get("protect_cores_for_target") or 0 for p in pp]
    rec = sizing.get("recommendation", {})
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar(range(len(labels)), cores, 0.5, color=C["protect"])
    r = rec.get("backend_cores_recommended")
    if r:
        ax.axhline(r, ls="--", color=C["crit"])
        ax.text(len(labels)-0.5, r, f" recommend {r} cores", va="bottom", ha="right", color=C["crit"], fontsize=9)
    ax.set_xticks(range(len(labels))); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("dedicated physical cores for 1M (PROTECT)")
    ax.set_title("Cores required to reach the target, by profile")
    ax.grid(axis="y", color=C["grid"]); ax.set_axisbelow(True)
    for i, v in enumerate(cores): ax.text(i, v, str(v), ha="center", va="bottom", fontsize=9)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agg-glob", required=True, help="glob for agg_*.json, e.g. results/agg_*.json")
    ap.add_argument("--sizing", required=True)
    ap.add_argument("--charts-dir", required=True)
    ap.add_argument("--target", type=float, default=1_000_000)
    args = ap.parse_args()

    os.makedirs(args.charts_dir, exist_ok=True)
    profiles = [json.load(open(p)) for p in sorted(glob.glob(args.agg_glob))]
    sizing = json.load(open(args.sizing)) if os.path.isfile(args.sizing) else {}
    d = args.charts_dir
    bar_throughput(profiles, args.target, os.path.join(d, "throughput.png"))
    bar_efficiency(profiles, os.path.join(d, "efficiency.png"))
    line_rolling(profiles, os.path.join(d, "rolling.png"))
    bar_steal(profiles, os.path.join(d, "steal.png"))
    if sizing:
        bar_sizing(sizing, os.path.join(d, "sizing.png"))
    print("charts written to %s" % d)


if __name__ == "__main__":
    main()

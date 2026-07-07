# Multi-Client Launcher  (Attribution Test A: is the client the bottleneck?)
#
# Spawns N independent CRDP_Stress.py processes concurrently against the same
# CRDP endpoint with identical workload parameters, then aggregates their JSON
# results. Separate OS processes (not threads) sidestep the Python GIL, so this
# is the definitive test for whether a single Python load generator is the wall.
#
# Interpretation:
#   - Aggregate cards/sec scales ~linearly with N        -> the single client
#     while system CPU climbs toward 100%                    was the bottleneck
#     (the load host is the wall; add more client hosts).
#   - Aggregate cards/sec plateaus while host CPU has     -> the wall is
#     headroom                                               downstream
#     (ingress or backend; proceed to Test B / Test C).
#
# Usage:
#   py multi_client.py -clients N [-label BASE] [-outdir DIR] \
#       -endpoint HOST -policy NAME -user NAME \
#       [-iterations N] [-batchsize N] [-threads N] [-charset ...] \
#       [-payload FILE | -csvlist FILE]
#
# Every argument other than -clients / -label / -outdir is forwarded verbatim
# to each child CRDP_Stress.py, so all of that tool's flags work here unchanged.
# Each child additionally receives its own -jsonout and -label.
#
# NOTE: The client-CPU figure captured by each child is SYSTEM-WIDE (psutil
# reports whole-host utilization), so with N co-located clients every child sees
# the same shared host CPU. That is exactly what Test A wants: it reveals when
# the load host itself saturates.
#
import argparse
import json
import os
import subprocess
import sys
import time

try:
    from termcolor import colored
except ImportError:  # keep the launcher usable even without termcolor
    def colored(s, *a, **k):
        return s


def parse_args():
    parser = argparse.ArgumentParser(
        description="Launch N concurrent CRDP_Stress.py clients and aggregate results (Attribution Test A)."
    )
    parser.add_argument("-clients", type=int, required=True,
                        help="Number of concurrent client processes to spawn.")
    parser.add_argument("-label", default="testA",
                        help="Base label; each child is tagged <label>-cN (default: testA).")
    parser.add_argument("-outdir", default=None,
                        help="Directory for per-child JSON + logs (default: ./multi_client_<label>).")
    # Everything else is forwarded to the child unchanged.
    known, passthrough = parser.parse_known_args()
    if known.clients < 1:
        print(colored("ERROR: -clients must be >= 1.", "red"))
        sys.exit(1)
    return known, passthrough


def main():
    known, passthrough = parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    child_script = os.path.join(script_dir, "CRDP_Stress.py")
    if not os.path.isfile(child_script):
        print(colored("ERROR: CRDP_Stress.py not found next to multi_client.py.", "red"))
        sys.exit(1)

    outdir = known.outdir or os.path.join(os.getcwd(), "multi_client_%s" % known.label)
    os.makedirs(outdir, exist_ok=True)

    print(colored("\n=== Attribution Test A: %d concurrent clients ===" % known.clients,
                  "white", attrs=["bold"]))
    print("  Forwarded args: %s" % " ".join(passthrough))
    print("  Output dir:     %s\n" % outdir)

    procs = []
    launch_start = time.time()
    for i in range(known.clients):
        json_path = os.path.join(outdir, "client_%d.json" % i)
        log_path = os.path.join(outdir, "client_%d.log" % i)
        child_label = "%s-c%d" % (known.label, i)

        cmd = [sys.executable, child_script] + passthrough + [
            "-jsonout", json_path,
            "-label", child_label,
        ]
        logf = open(log_path, "w")
        # Child stdout/stderr (colored text + tqdm bars) go to the per-child log
        # so the aggregate view stays clean and failures remain debuggable.
        p = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT, cwd=script_dir)
        procs.append({"idx": i, "proc": p, "json": json_path, "log": log_path, "logf": logf})
        print("  launched client %d -> %s" % (i, os.path.basename(json_path)))

    print(colored("\n  Waiting for %d clients to finish..." % known.clients, "cyan"))
    for entry in procs:
        entry["rc"] = entry["proc"].wait()
        entry["logf"].close()
    launch_end = time.time()

    # ----- Collect results -----
    results = []
    for entry in procs:
        if entry["rc"] != 0:
            print(colored("  client %d FAILED (rc=%d) - see %s"
                          % (entry["idx"], entry["rc"], entry["log"]), "red"))
            continue
        if not os.path.isfile(entry["json"]):
            print(colored("  client %d produced no JSON - see %s"
                          % (entry["idx"], entry["log"]), "red"))
            continue
        try:
            with open(entry["json"]) as jf:
                results.append(json.load(jf))
        except (OSError, ValueError) as e:
            print(colored("  client %d JSON unreadable: %s" % (entry["idx"], e), "red"))

    if not results:
        print(colored("\nNo successful clients - nothing to aggregate. Check the .log files.", "red"))
        sys.exit(1)

    print(colored("\n  %d/%d clients succeeded (total launcher wall: %.1fs)\n"
                  % (len(results), known.clients, launch_end - launch_start), "cyan"))

    aggregate_phase(results, "protect")
    aggregate_phase(results, "reveal")

    print(colored("\nInterpretation: linear cards/sec scaling vs N (with host CPU -> 100%%) means",
                  "white"))
    print(colored("the single client was the wall. A plateau with CPU headroom points downstream",
                  "white"))
    print(colored("(ingress/backend) - proceed to Test B (NodePort bypass) / Test C (kubectl top).\n",
                  "white"))


def aggregate_phase(results, phase_key):
    phases = [r[phase_key] for r in results if phase_key in r]
    if not phases:
        return

    per_client = [p.get("cards_per_sec", 0) for p in phases]
    total_cards = sum(p.get("total_cards", 0) for p in phases)
    sum_of_rates = sum(per_client)

    # Rigorous overlapped-window rate: total cards across all clients divided by
    # the union wall-clock window (earliest phase start -> latest phase end).
    starts = [p["wall_start_epoch"] for p in phases if p.get("wall_start_epoch")]
    ends = [p["wall_end_epoch"] for p in phases if p.get("wall_end_epoch")]
    window_rate = None
    overlap_note = ""
    if starts and ends:
        window = max(ends) - min(starts)
        if window > 0:
            window_rate = total_cards / window
        # How well did the clients actually overlap? Low overlap weakens the
        # sum-of-rates figure (clients ran staggered, not truly concurrent).
        max_wall = max(p.get("wall_time_sec", 0) for p in phases)
        if window > 0 and max_wall > 0:
            overlap_note = "  (window %.1fs vs longest client %.1fs)" % (window, max_wall)

    # System-wide client-host CPU as seen by the children (all see the same host).
    cpu_peaks = [p["client_cpu"]["peak"] for p in phases
                 if p.get("client_cpu", {}).get("available")]
    cpu_line = ""
    if cpu_peaks:
        cores = next((p["client_cpu"]["cores"] for p in phases
                      if p.get("client_cpu", {}).get("available")), 0)
        cpu_line = "  Host CPU peak (system-wide): %.0f%%  (of %d logical cores)" % (
            max(cpu_peaks), cores)

    print(colored("  --- %s ---" % phase_key.upper(), "white", attrs=["bold"]))
    print(colored("  Aggregate throughput (sum of client rates): %s cards/sec"
                  % _fmt(sum_of_rates), "green", attrs=["bold"]))
    if window_rate is not None:
        print(colored("  Aggregate throughput (overlapped window):    %s cards/sec%s"
                      % (_fmt(window_rate), overlap_note), "green"))
    print("  Total cards: %s across %d clients" % (_fmt(total_cards), len(phases)))
    print("  Per-client cards/sec: min %s | mean %s | max %s" % (
        _fmt(min(per_client)), _fmt(sum_of_rates / len(per_client)), _fmt(max(per_client))))
    if cpu_line:
        print(cpu_line)
    print()


def _fmt(n):
    return "{:,.0f}".format(n)


if __name__ == "__main__":
    main()

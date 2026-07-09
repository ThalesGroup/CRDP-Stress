# Spread Launcher  (benchmark data collection — runs ON the load host, e.g. cm-neptune)
#
# Spawns N CRDP_Stress.py client processes concurrently, round-robin across a set
# of CRDP endpoints (the three node NodePorts), each child writing its own -jsonout.
# On completion it re-reads the per-child JSONs and prints the aggregate throughput
# for PROTECT and REVEAL using the shared multi_client.aggregate_phase() logic.
#
# This is the load-generation half of the benchmark. Backend CPU + steal sampling is
# done separately (from the orchestrating host) and correlated later by
# aggregate_profile.py using each child's wall_start_epoch / wall_end_epoch.
#
# Usage:
#   python3 spread_launcher.py \
#       -clients 8 -endpoints 192.168.1.188:32085,192.168.1.187:32085,192.168.1.189:32085 \
#       -policy CRDP_Digitsonly_Protection -user alice -charset DIGITSONLY \
#       -iterations 1000000 -batchsize 5000 -threads 20 \
#       -outdir /tmp/bench/digits-canonical -label digits
#
import argparse
import json
import os
import subprocess
import sys
import time

# Import aggregate_phase from the sibling CRDP_Stress_App package.
_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(os.path.dirname(_HERE), "CRDP_Stress_App")
sys.path.insert(0, _APP)
import multi_client as mc  # noqa: E402  (reuse aggregate_phase)


def parse_args():
    p = argparse.ArgumentParser(description="Round-robin spread launcher for CRDP benchmark runs.")
    p.add_argument("-clients", type=int, required=True, help="Number of concurrent client processes.")
    p.add_argument("-endpoints", required=True,
                   help="Comma-separated CRDP endpoints (HOST:PORT) to round-robin clients across.")
    p.add_argument("-policy", required=True, help="CRDP protection policy name.")
    p.add_argument("-user", default="alice", help="Reveal username.")
    p.add_argument("-charset", default="DIGITSONLY",
                   choices=["DIGITSONLY", "ALPHANUMERIC", "PRINTABLEASCII"],
                   help="Client plaintext charset (must match FPE policy format).")
    p.add_argument("-iterations", type=int, default=1000000, help="Payloads per client.")
    p.add_argument("-batchsize", type=int, default=5000, help="Payloads per bulk REST call.")
    p.add_argument("-threads", type=int, default=20, help="Worker threads per client process.")
    p.add_argument("-outdir", required=True, help="Directory for per-child JSON + logs.")
    p.add_argument("-label", default="run", help="Base label; children tagged <label>-cN.")
    return p.parse_args()


def main():
    a = parse_args()
    endpoints = [e.strip() for e in a.endpoints.split(",") if e.strip()]
    if not endpoints:
        print("ERROR: no endpoints", file=sys.stderr)
        sys.exit(1)

    child_script = os.path.join(_APP, "CRDP_Stress.py")
    if not os.path.isfile(child_script):
        print("ERROR: CRDP_Stress.py not found at %s" % child_script, file=sys.stderr)
        sys.exit(1)

    os.makedirs(a.outdir, exist_ok=True)

    print("=== spread_launcher: %d clients over %d endpoints ===" % (a.clients, len(endpoints)))
    print("  policy=%s charset=%s iters=%d batch=%d threads=%d" %
          (a.policy, a.charset, a.iterations, a.batchsize, a.threads))
    print("  endpoints=%s" % ", ".join(endpoints))
    print("  outdir=%s" % a.outdir)

    procs = []
    for i in range(a.clients):
        endpoint = endpoints[i % len(endpoints)]
        jpath = os.path.join(a.outdir, "client_%d.json" % i)
        lpath = os.path.join(a.outdir, "client_%d.log" % i)
        cmd = [sys.executable, child_script,
               "-endpoint", endpoint, "-policy", a.policy, "-user", a.user,
               "-charset", a.charset,
               "-iterations", str(a.iterations), "-batchsize", str(a.batchsize),
               "-threads", str(a.threads),
               "-jsonout", jpath, "-label", "%s-c%d" % (a.label, i)]
        lf = open(lpath, "w")
        procs.append((subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=_APP), jpath, lf, endpoint, i))
        print("  launched client %d -> %s" % (i, endpoint))

    print("  waiting for %d clients ..." % a.clients, flush=True)
    for proc, jpath, lf, endpoint, i in procs:
        rc = proc.wait()
        lf.close()
        if rc != 0:
            print("  client %d FAILED rc=%d (see log)" % (i, rc))

    results = []
    for proc, jpath, lf, endpoint, i in procs:
        if os.path.isfile(jpath):
            try:
                with open(jpath) as jf:
                    results.append(json.load(jf))
            except (OSError, ValueError) as e:
                print("  client %d JSON unreadable: %s" % (i, e))

    print("\nsucceeded: %d / %d" % (len(results), a.clients))
    if not results:
        print("NO successful clients — check logs in %s" % a.outdir)
        sys.exit(1)

    for phase in ("protect", "reveal"):
        mc.aggregate_phase(results, phase)


if __name__ == "__main__":
    main()

**Welcome to a python-based CRDP stressing utility**

It works fairly simply.  It creates random plaintext data and then submits that to CRDP to determine how long CRDP takes to protect (encrypt) or reveal (decrypt).

Every PROTECT/REVEAL call goes through the CRDP bulk API. The total plaintext workload is split into messages of `-batchsize` payloads each, and (with multiple threads) messages are distributed round-robin across workers.

**Project layout:**

```
CRDP_Stress_App/      # Python stress-test app (run from here)
  CRDP_Stress.py
  CRDP_REST_API.py
  parallel_execution.py
  multi_client.py       # launches N stress processes on one host (beats the GIL)
  requirements.txt
CRDP_K8_Deployment/   # Kubernetes manifests + deploy script for CRDP
  crdp-app-svc-ing.yml
  crdp-ingress.yml
  makeSecretandDeploy.sh
Test_Data/            # Sample inputs for -payload / -csvlist
  RAM_Image.jpg
  plaintext.txt
  plaintext.zip
  testpatterns.csv
```

Usage:
**py CRDP_Stress.py [-h] -endpoint ENDPOINTCRDP -policy PROTECTIONPOLICY [-iterations ITERATIONS] -user USERNAME [-batchsize BATCHSIZE] [-charset {ALPHANUMERIC, DIGITSONLY, PRINTABLEASCII}] [-threads THREADCOUNT] [-jsonout FILENAME] [-label NAME] [-payload FILENAME | -csvlist FILENAME]** where:

-endpoint ENDPOINTCRDP - The host name (or IP address) and port (optional) where CRDP is hosted. Typically the value of `$CRDP_HOST` from the deploy script (defaults to `crdp.local`).

-policy PROTECTIONPOLICY - The name of the Protection Policy that has been defined in CRDP. E.g., CRDP-DP-Policy1

-iterations ITERATIONS - How many times the protection / reveal action should be performed during the test.
                        Defaults to 1 if omitted in any mode. In CSV mode, iterations N causes the full
                        set of CSV cells to be re-processed N times (the `_protected.csv` output is
                        written once, from the first pass).

-user USERNAME      - The name of the user that will be used during the REVEAL test

-batchsize BATCHSIZE - Number of plaintext payloads sent in a single message to CRDP's bulk API.
                        Defaults to 1 (one payload per call). Use 0 to send all plaintext payloads
                        in a single message. The total payload pool is split into ceil(total/batchsize)
                        messages, and the last message contains the remainder if the total is not a
                        clean multiple of batchsize.

-charset (optional) - Character set used when random plaintext needs to be generated. Defaults to
                      DIGITSONLY. **Ignored when `-payload` or `-csvlist` is supplied** since the
                      plaintext comes from the file in those modes.
            ALPHANUMERIC - generate alphanumeric data
            DIGITSONLY - generate characters only using numeric digits (formatted like a credit card)
            PRINTABLEASCII - generate plaintext consisting of any printable character (including $pecial characters)

-threads THREADCOUNT - Number of concurrent client threads sending data to CRDP. Messages are
                        distributed round-robin across threads; each thread sends one message at a
                        time until all messages are sent. Capped to the number of messages — there
                        is no benefit in idle workers.

-jsonout FILENAME   - (optional) Write a machine-readable JSON results file for run-to-run
                        comparison. Captures, per phase (PROTECT/REVEAL): throughput in txns/sec,
                        MB/s, per-bulk-call latency percentiles (p50/p95/p99/max), a rolling
                        txns/sec time series, worker load skew, and client-host CPU (avg/peak).
                        Useful for the throughput attribution matrix (client vs ingress vs backend).

-label NAME         - (optional) A tag recorded in the -jsonout file to identify the run
                        (e.g. `testA-4clients`). Has no effect unless -jsonout is also supplied.

> The client-host CPU line (in both the on-screen summary and the -jsonout file) requires the
> `psutil` package (`pip install -r requirements.txt`). If psutil is not installed the run still
> works and every other metric is captured; the CPU line just reports "not captured".

-payload FILENAME   - Supply an actual file (text or binary) that is encrypted in its entirety
                        as a single payload. With `-iterations N`, each message contains `batchsize`
                        copies of the file and the total number of messages is N / batchsize.
                        After the PROTECT and REVEAL round trip completes, the protected payload is
                        written to a copy of the file with "_protected" appended to the name (e.g.
                        RAM_Image.jpg -> RAM_Image_protected.jpg), overwriting any existing file of
                        that name.

-csvlist FILENAME   - Supply a CSV file. The first row is treated as a header and preserved
                        as-is; every data cell in the remaining rows is protected/tokenized. With
                        `-iterations N > 1`, the CSV is re-processed N times for stress, but the
                        `_protected` copy is written once (from the first pass) with "_protected"
                        appended to the name (e.g. data.csv -> data_protected.csv), overwriting any
                        existing file of that name. -payload and -csvlist are mutually exclusive.

**How -iterations, -batchsize, and -threads combine:**

- `-iterations` controls the **total number of plaintext payloads** to process (in CSV mode: cells × iterations).
- `-batchsize` controls **how many payloads go in each bulk REST call** (0 = all in one call).
- `-threads` controls the **degree of parallelism** — messages are distributed round-robin across workers.

If the message count is smaller than the thread count, the thread count is automatically capped to match.

| Flags provided                                              | Behavior                                                                                          |
|-------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| `-iterations 100 -batchsize 1 -threads 50`                  | 100 messages of 1 payload each, distributed across 50 workers (~2 messages per worker).           |
| `-iterations 100 -batchsize 10 -threads 50`                 | 10 messages of 10 payloads each, thread count capped to 10 (one message per worker).              |
| `-iterations 1000 -batchsize 100 -threads 10`               | 10 messages of 100 payloads each, one per worker.                                                 |
| `-iterations 1000 -batchsize 0`                             | A single bulk message containing all 1000 payloads (sequential since 1 message).                  |
| `-payload f.bin -iterations 200 -batchsize 50 -threads 4`   | 4 messages of 50 file copies each, one per worker.                                                |
| `-csvlist data.csv -batchsize 100`                          | Total CSV cells split into messages of 100 each; sequential single thread.                        |
| `-csvlist data.csv -iterations 5 -batchsize 100 -threads 8` | (cells × 5) total payloads, chopped into messages of 100, distributed across 8 workers.           |

**Examples:**

Run from the `CRDP_Stress_App/` folder. Test files live in `../Test_Data/`. The examples below pass `$CRDP_HOST` for `-endpoint` — that variable is set by `makeSecretandDeploy.sh` in the same shell session, or you can export it manually (`export CRDP_HOST=<your-crdp-ip-or-fqdn>`) before running.

```bash
cd CRDP_Stress_App

# Stress test with random data: 10,000 payloads in messages of 100, across 100 parallel workers
python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy MyPolicy -user alice -iterations 10000 -batchsize 100 -threads 100

# File stress test: 1000 copies of the image, 10 messages of 100 copies each, one per worker
python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy MyPolicy -user alice -payload ../Test_Data/RAM_Image.jpg -iterations 1000 -batchsize 100 -threads 10

# File stress test (one payload per call): 200 messages, 50 workers (~4 messages each)
python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy MyPolicy -user alice -payload ../Test_Data/RAM_Image.jpg -iterations 200 -batchsize 1 -threads 50

# Quick file test: one call total
python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy MyPolicy -user alice -payload ../Test_Data/RAM_Image.jpg

# CSV list: protect every cell of testpatterns.csv (messages of 50), write testpatterns_protected.csv
python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy MyPolicy -user alice -csvlist ../Test_Data/testpatterns.csv -batchsize 50 -threads 10
```

**Recommended settings for credit-card throughput (16-digit card, 3-node / 24-pod cluster):**

Measured against the tuned 3-node cluster (48 CPU cores total; 24 CRDP pods with CPU requests and an
even pod spread — see [crdp-app-svc-ing.yml](CRDP_K8_Deployment/crdp-app-svc-ing.yml)). Two client
knobs drive throughput: `-batchsize` (txns per bulk REST call) and concurrency. Concurrency comes in
two forms — `-threads` (worker threads inside one Python process, capped by the GIL) and **processes**
(independent OS processes launched by `multi_client.py`, which sidestep the GIL). The optimal mix
depends on which layer is the bottleneck, and that is decided by the protection policy's cipher:

| Protection policy | Cipher | Bottleneck | `-batchsize` | `-threads` (per process) | Client processes |
|---|---|---|---|---|---|
| FPE_AES (e.g. `CRDP_Digitsonly_Protection`) | format-preserving AES | **backend CPU** | 5000 | 20 | 4–6 |
| AES/CBC (e.g. `CRDP_Binary_Protection`) | AES-256-CBC | **client / load host** | 5000 | 20 | ~1 per physical core of the load host (≈6–8 on a 12-core box); add load hosts to scale further |

- **Batch size ≈ 5000** is the sweet spot for both policies. Smaller adds per-call overhead; much
  larger (20k–40k) *reduces* throughput because the last oversized message per worker creates tail
  imbalance (one node drains while the others sit idle).
- **Threads plateau around 20.** Past ~24 the GIL serializes the per-call JSON encode/decode, so extra
  threads add nothing (throughput was flat from 24→48 threads in testing). To go faster, add
  **processes** (via `multi_client.py`), not threads.
- **FPE_AES is backend-bound** (~650k txns/sec on 48 cores): ~5 client processes fully saturate the
  cluster and more just contend. Reaching 1M/sec with FPE needs **more nodes**, not more client load.
- **AES-256-CBC costs roughly half the CPU per txn** (~1.3M txns/sec ceiling on the same 48 cores),
  so the *load host* becomes the limit. Use one process per physical core, keep `orjson` installed
  (it releases the GIL for JSON), and add load hosts to reach the ceiling.

Near-optimal FPE run — 6 client processes:
```bash
cd CRDP_Stress_App
python3 multi_client.py -clients 6 -endpoint $CRDP_HOST \
  -policy CRDP_Digitsonly_Protection -user alice \
  -iterations 2000000 -batchsize 5000 -threads 20
```

**Kubernetes Deployment**

The `CRDP_K8_Deployment/` folder contains Kubernetes manifests and a deployment script for running CRDP across a multi-node, multi-pod Kubernetes cluster (plain `kubectl` by default; MicroK8s via the `--microk8s` flag):

- **crdp-app-svc-ing.yml** — Deployment (24 replicas, with CPU requests and `topologySpreadConstraints` for even placement) and NodePort Service for CRDP.
- **crdp-ingress.yml** — Ingress resource for host-based routing via the NGINX Ingress Controller. The `host:` field is templated as `${CRDP_HOST}` and filled in at apply time.
- **makeSecretandDeploy.sh** — Deployment script that:
  1. Creates the `crdp-secret-name` Kubernetes secret from the CRDP App registration token.
  2. Ensures the NGINX Ingress Controller is installed — installs it from the official manifest (`ingress-nginx` v1.11.2, bare-metal variant) if absent, and patches it for `hostNetwork=true` so it binds to port 80. Aborts on any install failure.
  3. Ensures `$CRDP_HOST` is resolvable on this host — by default edits `/etc/hosts` (adds the line via `sudo` if missing); with `--fqdn <name>` it verifies the name already resolves via `getent hosts` and leaves `/etc/hosts` untouched.
  4. Applies the Deployment and Service from crdp-app-svc-ing.yml (substituting `KEY_MANAGER_HOST`).
  5. Applies the Ingress resource from crdp-ingress.yml (substituting `CRDP_HOST`).

**Configuration — Environment Variables:**

The deploy script reads three environment variables. If any is unset it will prompt or default, as noted below. The script uses `envsubst` (from `gettext`) to inject these values into the YAMLs at apply time, so `envsubst` must be installed (`sudo apt install gettext-base` on Debian/Ubuntu).

| Variable | Purpose | Behavior if unset |
|---|---|---|
| `REG_TOKEN_VALUE` | CRDP App Registration Token from CipherTrust Manager. Treat as a credential — do **not** commit it to source control. | **Silent prompt** (input is not echoed). Aborts if empty. |
| `KEY_MANAGER_HOST` | IP or FQDN of the CipherTrust Manager that CRDP pods register against. Lands in the `KEY_MANAGER_HOST` env of every CRDP pod. | **Echoed prompt.** Aborts if empty. |
| `CRDP_HOST` | Hostname (FQDN) clients use to reach CRDP. Lands in the Ingress `host:` field. **Must be a hostname**, not an IP — the Kubernetes API rejects IP addresses in `host:` at admission. | **Defaults to `crdp.local`.** Echoed back so you can confirm. Override by exporting `CRDP_HOST=<your-fqdn>` before running, or by passing `--fqdn <name>` / `-f <name>` (which additionally skips the `/etc/hosts` edit and requires the name to already resolve via DNS). |

> Where to get `REG_TOKEN_VALUE`: in CipherTrust Manager, open the CRDP App registration and copy the registration token.

**Targeting MicroK8s (`--microk8s`):**

By default the script calls plain `kubectl`. Pass `--microk8s` (or `-m`) when the cluster is a MicroK8s install — the script then routes every cluster operation through `microk8s kubectl` instead. The script validates the chosen binary is on `PATH` and aborts with a clear error if not.

```bash
./makeSecretandDeploy.sh --microk8s
```

**DNS mode (`--fqdn`):**

Pass `--fqdn <name>` (or `-f <name>`) when `$CRDP_HOST` is already published through DNS. The script then skips the `/etc/hosts` edit, doesn't need `sudo` for hosts-file modification, and verifies resolution with `getent hosts` before applying anything to the cluster. Every client calling CRDP must also be able to resolve the name.

```bash
./makeSecretandDeploy.sh --fqdn crdp.example.com
```

If the FQDN does not resolve at install time the script aborts with a clear error so a typo cannot produce a silently-unreachable Ingress. If both `--fqdn` and the `CRDP_HOST` env var are set to different values, the flag wins and the script emits a warning.

**To deploy:**

1. Optionally export any env var beforehand (especially useful for unattended runs):
   ```bash
   export REG_TOKEN_VALUE=<token-from-CipherTrust-Manager>
   export KEY_MANAGER_HOST=<ciphertrust-manager-ip-or-fqdn>
   export CRDP_HOST=<your-fqdn>   # optional; defaults to crdp.local
   ```
2. Run the script from the deployment folder (it references the YAMLs by relative path):
   ```bash
   cd CRDP_K8_Deployment
   ./makeSecretandDeploy.sh
   ```
   You may be prompted for your sudo password (used only to append the `$CRDP_HOST` entry to `/etc/hosts` if not already present; skipped in `--fqdn` mode). The script prints the final URL (`http://$CRDP_HOST`) and a ready-to-use stress-test command at the end.
3. **For other clients** — in the default mode the script only updates `/etc/hosts` on the host where it runs. To call CRDP from any other client, add the same line on that client (or set up DNS):
   ```
   <deploy-host-ip>  crdp.local
   ```
   If you maintain DNS centrally, use `--fqdn <name>` on the deploy host instead — then no client needs an `/etc/hosts` edit.

**About the NGINX Ingress Controller:**

The script uses the upstream `ingress-nginx` project (`kubernetes/ingress-nginx`), not the MicroK8s `ingress` addon — on recent MicroK8s versions that addon installs Traefik instead, which has a different IngressClass and configuration. If NGINX is already installed (detected by the presence of an `IngressClass` named `nginx`), the script skips the install and proceeds directly to applying the manifests.

**Alternative: NodePort-only access (no Ingress):**

If you do not want the Ingress (e.g. you want to reach CRDP by IP directly), CRDP is still exposed as a NodePort service at `http://<any-node-ip>:32085`. To use this path, comment out the final `envsubst < crdp-ingress.yml | $KUBECTL apply -f -` line in `makeSecretandDeploy.sh` and skip the `/etc/hosts` step.

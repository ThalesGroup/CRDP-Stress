**Welcome to a python-based CRDP stressing utility**

It works fairly simply.  It creates random plaintext data and then submits that to CRDP to determine how long CRDP takes to protect (encrypt) or reveal (decrypt).

Command line parameters allow the user to specify the number of times to repeat the encryption process as well as whether the protection process is record-by-record or as a bulk submission (which always performs faster).

Usage:
**py CRDP_Stress.py [-h] -e HOSTNAMECRDP -p PROTECTIONPOLICY [-b BATCHSIZE] -u USERNAME [-bulk] [-c {ALPHANUMERIC, DIGITSONLY, PRINTABLEASCII}] [-t TASKCOUNT] [-f FILENAME]** where:

-e HOSTNAME         - The host name (or IP address) and port (optional) where CRDP is hosted.  E.g., crdp.test256.io

-p PROTECTIONPOLICY - The name of the Protection Policy that has been defined in CRDP. E.g., CRDP-DP-Policy1

-b BATCHSIZE        - How many times the protection / reveal action should be performed during the test.
                        This is an integer between 1 and 1,000,000.
                        Note that NON-bulk testing can take a LONG TIME with large batch sizes.

-u USERNAME         - The name of the user that will be used during the REVEAL test

-c (optional)
            ALPHANUMERIC - for plaintext, generate alphanumeric data
            DIGITSONLY - for plaintext, generate characters only using numeric digits
            PRINTABLEASCII - for plaintext, generate plaintext consisting of any printable character (including $pecial characters)

[-bulk]             - just a FLAG that indicates whether the test should be formed as a bulk submission

-t TASKCOUNT        - To stress CRDP when multiple pods are deployed, TASKCOUNT will take the BATCHSIZE and divide it
                        by the TASKCOUNT and then issue a PROTECT/REVEAL task for each task in TASKCOUNT.  E.g., if
                        your BATCHSIZE is 10,000 and your TASKCOUNT is 10, then 10 TASKS will be independently started
                        with a batch size of 1000 per TASK (either discretely or as bulk payloads).

-f FILENAME         - Supply an actual file for encryption (text or binary).

**Combining -f, -b, -t, and -bulk:**

When a file (-f) is provided, the -b (batch size), -t (task count), and -bulk flags work together:

- `-b` controls the **total number** of file operations (how many times the file is processed).
- `-t` controls the **degree of parallelism** (how many workers run concurrently).
- `-bulk` controls whether each worker sends its items in a **single bulk API call** or as **individual sequential calls**.

If the batch size is smaller than the task count, the task count is automatically capped to match
the batch size (there is no benefit in having idle workers).

| Flags provided                      | Behavior                                                                                                |
|-------------------------------------|---------------------------------------------------------------------------------------------------------|
| `-f -b 100 -t 50`                  | 100 discrete protect calls, distributed across 50 workers (~2 sequential calls each).                   |
| `-f -b 100 -t 50 -bulk`            | 50 workers each send 1 bulk request containing ~2 file copies (100 total, submitted as bulk payloads).  |
| `-f -b 30 -t 500`                  | 30 discrete calls. Task count capped at 30 (one per batch item).                                        |
| `-f -b 30 -t 500 -bulk`            | 30 workers each send 1 bulk request with 1 file copy. -bulk has no additional effect (same as above).   |
| `-f -t 500`                        | File processed 500 times (once per worker), as individual discrete calls.                               |
| `-f -t 500 -bulk`                  | File processed 500 times. Each worker sends 1 bulk request with 1 file copy (same throughput).          |
| `-f -t 10 -b 1000 -bulk`           | 10 workers each send 1 bulk request containing 100 file copies (1000 total, submitted as bulk payloads).|
| `-f` only                           | The file is processed once, sequentially, as a single discrete call.                                    |
| `-f -b 100`                         | The file is processed 100 times sequentially as individual calls (task count defaults to 1).            |
| `-f -b 100 -bulk`                   | The file is processed 100 times as a single sequential bulk call containing all 100 copies.             |

**Examples:**

```bash
# Stress test with random data: 10,000 bulk protect/reveal calls across 100 parallel workers
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -b 10000 -t 100 -bulk

# File stress test: 1000 copies of the image, 10 workers each sending a bulk request of 100
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -f RAM_Image.jpg -b 1000 -t 10 -bulk

# File stress test (discrete): 200 individual protect calls across 50 workers (~4 each)
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -f RAM_Image.jpg -b 200 -t 50

# Quick file test: one call per worker across 500 parallel workers
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -f RAM_Image.jpg -t 500
```

**Kubernetes Deployment**

A set of Kubernetes manifests and a deployment script are included for running CRDP across a multi-node, multi-pod MicroK8s cluster:

- **crdp-app-svc-ing.yml** — Deployment (6 replicas) and NodePort Service for CRDP.
- **crdp-ingress.yml** — Ingress resource for host-based routing via the NGINX Ingress Controller at crdp.test256.io.
- **makeSecretandDeploy.sh** — Deployment script that:
  1. Creates the `crdp-secret-name` Kubernetes secret from the CRDP App registration token.
  2. Applies the Deployment and Service from crdp-app-svc-ing.yml.
  3. Enables the MicroK8s NGINX Ingress Controller addon if it is not already deployed.
  4. Patches the Ingress Controller DaemonSet to use `hostNetwork=true` if needed (required on MicroK8s v1.33.9 where the addon does not set this by default).
  5. Applies the Ingress resource from crdp-ingress.yml for load-balanced routing.

**To deploy:**

1. Edit `makeSecretandDeploy.sh` and replace the `REG_TOKEN_VALUE` with the registration token from CipherTrust Manager for the CRDP App.
2. Run the script:
   ```bash
   ./makeSecretandDeploy.sh
   ```
3. On each client machine, map `crdp.test256.io` to one or more node IPs in DNS or `/etc/hosts`:
   ```
   192.168.1.188  crdp.test256.io
   192.168.1.187  crdp.test256.io
   ```
   Adding both node IPs provides round-robin DNS for client-to-ingress load distribution. NGINX always load-balances across all CRDP backend pods regardless of which node the request enters on.

**Alternative: NodePort-only access (no Ingress):**

If the Ingress Controller is not available, CRDP is still reachable directly via the NodePort service at `http://<any-node-ip>:32085`. To use this path, comment out the `kubectl apply -f crdp-ingress.yml` line in makeSecretandDeploy.sh.

**Welcome to a python-based CRDP stressing utility**

It works fairly simply.  It creates random plaintext data and then submits that to CRDP to determine how long CRDP takes to protect (encrypt) or reveal (decrypt).

Command line parameters allow the user to specify the number of times to repeat the encryption process as well as whether the protection process is record-by-record or as a bulk submission (which always performs faster).

Usage:
**py CRDP_Stress.py [-h] -e HOSTNAMECRDP -p PROTECTIONPOLICY [-batchsize BATCHSIZE] -u USERNAME [-bulk] [-c {ALPHANUMERIC, DIGITSONLY, PRINTABLEASCII}] [-threads THREADCOUNT] [-payload FILENAME | -csvlist FILENAME]** where:

-e HOSTNAME         - The host name (or IP address) and port (optional) where CRDP is hosted.  E.g., crdp.test256.io

-p PROTECTIONPOLICY - The name of the Protection Policy that has been defined in CRDP. E.g., CRDP-DP-Policy1

-batchsize BATCHSIZE - How many times the protection / reveal action should be performed during the test.
                        This is an integer between 1 and 1,000,000.
                        Note that NON-bulk testing can take a LONG TIME with large batch sizes.

-u USERNAME         - The name of the user that will be used during the REVEAL test

-c (optional)
            ALPHANUMERIC - for plaintext, generate alphanumeric data
            DIGITSONLY - for plaintext, generate characters only using numeric digits
            PRINTABLEASCII - for plaintext, generate plaintext consisting of any printable character (including $pecial characters)

[-bulk]             - just a FLAG that indicates whether the test should be formed as a bulk submission

-threads THREADCOUNT - To stress CRDP when multiple pods are deployed, THREADCOUNT will take the BATCHSIZE and divide it
                        by the THREADCOUNT and then issue a PROTECT/REVEAL task for each thread in THREADCOUNT.  E.g., if
                        your BATCHSIZE is 10,000 and your THREADCOUNT is 10, then 10 THREADS will be independently started
                        with a batch size of 1000 per THREAD (either discretely or as bulk payloads).

-payload FILENAME   - Supply an actual file (text or binary) that is encrypted in its entirety
                        as a single payload. Replaces the former -f flag. After the PROTECT and
                        REVEAL round trip completes, the protected payload is written to a copy
                        of the file with "_protected" appended to the name (e.g. RAM_Image.jpg ->
                        RAM_Image_protected.jpg), overwriting any existing file of that name.

-csvlist FILENAME   - Supply a CSV file. The first row is treated as a header and preserved
                        as-is; every data cell in the remaining rows is protected/tokenized.
                        After the normal round-trip stress test completes, a copy of the file
                        is written with "_protected" appended to the name (e.g. data.csv ->
                        data_protected.csv), overwriting any existing file of that name. Each
                        row in that file contains the protected equivalent of the source cells.
                        -payload and -csvlist are mutually exclusive.

**Combining -payload, -batchsize, -threads, and -bulk:**

When a file (-payload) is provided, the -batchsize, -threads (thread count), and -bulk flags work together:

- `-batchsize` controls the **total number** of file operations (how many times the file is processed).
- `-threads` controls the **degree of parallelism** (how many workers run concurrently).
- `-bulk` controls whether each worker sends its items in a **single bulk API call** or as **individual sequential calls**.

If the batch size is smaller than the task count, the task count is automatically capped to match
the batch size (there is no benefit in having idle workers).

| Flags provided                      | Behavior                                                                                                |
|-------------------------------------|---------------------------------------------------------------------------------------------------------|
| `-payload -batchsize 100 -threads 50`        | 100 discrete protect calls, distributed across 50 workers (~2 sequential calls each).                   |
| `-payload -batchsize 100 -threads 50 -bulk`  | 50 workers each send 1 bulk request containing ~2 file copies (100 total, submitted as bulk payloads).  |
| `-payload -batchsize 30 -threads 500`        | 30 discrete calls. Thread count capped at 30 (one per batch item).                                      |
| `-payload -batchsize 30 -threads 500 -bulk`  | 30 workers each send 1 bulk request with 1 file copy. -bulk has no additional effect (same as above).   |
| `-payload -threads 500`                      | File processed 500 times (once per worker), as individual discrete calls.                               |
| `-payload -threads 500 -bulk`                | File processed 500 times. Each worker sends 1 bulk request with 1 file copy (same throughput).          |
| `-payload -threads 10 -batchsize 1000 -bulk` | 10 workers each send 1 bulk request containing 100 file copies (1000 total, submitted as bulk payloads).|
| `-payload` only                               | The file is processed once, sequentially, as a single discrete call.                                    |
| `-payload -batchsize 100`                     | The file is processed 100 times sequentially as individual calls (thread count defaults to 1).          |
| `-payload -batchsize 100 -bulk`               | The file is processed 100 times as a single sequential bulk call containing all 100 copies.             |

**-csvlist mode:**

`-csvlist` does not use `-batchsize` (the workload size is the number of data cells in the CSV).
`-threads` and `-bulk` still control parallelism and whether cells are submitted individually
or as bulk payloads. The protected `_protected` copy is written once, after the full
protect/reveal round trip completes.

**Examples:**

```bash
# Stress test with random data: 10,000 bulk protect/reveal calls across 100 parallel workers
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -batchsize 10000 -threads 100 -bulk

# File stress test: 1000 copies of the image, 10 workers each sending a bulk request of 100
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -payload RAM_Image.jpg -batchsize 1000 -threads 10 -bulk

# File stress test (discrete): 200 individual protect calls across 50 workers (~4 each)
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -payload RAM_Image.jpg -batchsize 200 -threads 50

# Quick file test: one call per worker across 500 parallel workers
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -payload RAM_Image.jpg -threads 500

# CSV list: protect every cell of customers.csv and write customers_protected.csv
python3 CRDP_Stress.py -e crdp.test256.io -p MyPolicy -u alice -csvlist customers.csv -threads 10 -bulk
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

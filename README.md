**Welcome to a python-based CRDP stressing utility**

It works fairly simply.  It creates random plaintext data and then submits that to CRDP to determine how long CRDP takes to protect (encrypt) or reveal (decrypt).

Every PROTECT/REVEAL call goes through the CRDP bulk API. The total plaintext workload is split into messages of `-batchsize` payloads each, and (with multiple threads) messages are distributed round-robin across workers.

**Project layout:**

```
CRDP_Stress_App/      # Python stress-test app (run from here)
  CRDP_Stress.py
  CRDP_REST_API.py
  parallel_execution.py
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
**py CRDP_Stress.py [-h] -endpoint ENDPOINTCRDP -policy PROTECTIONPOLICY [-iterations ITERATIONS] -user USERNAME [-batchsize BATCHSIZE] [-charset {ALPHANUMERIC, DIGITSONLY, PRINTABLEASCII}] [-threads THREADCOUNT] [-payload FILENAME | -csvlist FILENAME]** where:

-endpoint ENDPOINTCRDP - The host name (or IP address) and port (optional) where CRDP is hosted.  E.g., crdp.test256.io

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
                      ALPHANUMERIC. **Ignored when `-payload` or `-csvlist` is supplied** since the
                      plaintext comes from the file in those modes.
            ALPHANUMERIC - generate alphanumeric data
            DIGITSONLY - generate characters only using numeric digits (formatted like a credit card)
            PRINTABLEASCII - generate plaintext consisting of any printable character (including $pecial characters)

-threads THREADCOUNT - Number of concurrent client threads sending data to CRDP. Messages are
                        distributed round-robin across threads; each thread sends one message at a
                        time until all messages are sent. Capped to the number of messages — there
                        is no benefit in idle workers.

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

Run from the `CRDP_Stress_App/` folder. Test files live in `../Test_Data/`.

```bash
cd CRDP_Stress_App

# Stress test with random data: 10,000 payloads in messages of 100, across 100 parallel workers
python3 CRDP_Stress.py -endpoint crdp.test256.io -policy MyPolicy -user alice -iterations 10000 -batchsize 100 -threads 100

# File stress test: 1000 copies of the image, 10 messages of 100 copies each, one per worker
python3 CRDP_Stress.py -endpoint crdp.test256.io -policy MyPolicy -user alice -payload ../Test_Data/RAM_Image.jpg -iterations 1000 -batchsize 100 -threads 10

# File stress test (one payload per call): 200 messages, 50 workers (~4 messages each)
python3 CRDP_Stress.py -endpoint crdp.test256.io -policy MyPolicy -user alice -payload ../Test_Data/RAM_Image.jpg -iterations 200 -batchsize 1 -threads 50

# Quick file test: one call total
python3 CRDP_Stress.py -endpoint crdp.test256.io -policy MyPolicy -user alice -payload ../Test_Data/RAM_Image.jpg

# CSV list: protect every cell of testpatterns.csv (messages of 50), write testpatterns_protected.csv
python3 CRDP_Stress.py -endpoint crdp.test256.io -policy MyPolicy -user alice -csvlist ../Test_Data/testpatterns.csv -batchsize 50 -threads 10
```

**Kubernetes Deployment**

The `CRDP_K8_Deployment/` folder contains Kubernetes manifests and a deployment script for running CRDP across a multi-node, multi-pod MicroK8s cluster:

- **crdp-app-svc-ing.yml** — Deployment (6 replicas) and NodePort Service for CRDP.
- **crdp-ingress.yml** — Ingress resource for host-based routing via the NGINX Ingress Controller at crdp.test256.io.
- **makeSecretandDeploy.sh** — Deployment script that:
  1. Creates the `crdp-secret-name` Kubernetes secret from the CRDP App registration token.
  2. Applies the Deployment and Service from crdp-app-svc-ing.yml.
  3. Enables the MicroK8s NGINX Ingress Controller addon if it is not already deployed.
  4. Patches the Ingress Controller DaemonSet to use `hostNetwork=true` if needed (required on MicroK8s v1.33.9 where the addon does not set this by default).
  5. Applies the Ingress resource from crdp-ingress.yml for load-balanced routing.

**To deploy:**

1. Edit `CRDP_K8_Deployment/makeSecretandDeploy.sh` and replace the `REG_TOKEN_VALUE` with the registration token from CipherTrust Manager for the CRDP App.
2. Run the script from the deployment folder (it references the YAMLs by relative path):
   ```bash
   cd CRDP_K8_Deployment
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

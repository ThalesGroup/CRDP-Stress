#!/bin/bash
#
# Deploys CRDP to a standard Kubernetes cluster (default) or to MicroK8s.
# The script:
#   1. Creates the crdp-secret-name Kubernetes secret from the CRDP App
#      Registration Token issued by CipherTrust Manager.
#   2. Applies the CRDP Deployment + Service (crdp-app-svc-ing.yml) after
#      substituting KEY_MANAGER_HOST.
#   3. Ensures the NGINX Ingress Controller is installed (installs it from the
#      official manifest if absent; aborts on any failure).
#   4. Ensures /etc/hosts on this host maps $CRDP_HOST to the host's primary IP.
#   5. Applies the Ingress (crdp-ingress.yml) after substituting CRDP_HOST.
#
# Flags:
#   --microk8s, -m      Use 'microk8s kubectl' instead of plain 'kubectl' for
#                       every cluster operation. Use this when targeting a
#                       MicroK8s installation.
#   --help, -h          Show usage and exit.
#
# Environment variables consumed (the script prompts or defaults if unset):
#   REG_TOKEN_VALUE   - CRDP App Registration Token from CipherTrust Manager.
#                       Prompted for silently if not set.
#   KEY_MANAGER_HOST  - IPv4 address of CipherTrust Manager. MUST be an IP, not
#                       an FQDN: CRDP pods resolve this via cluster DNS, which
#                       does not consult the node's /etc/hosts. If unset or set
#                       to a non-IP value, the script prompts for an IP.
#   CRDP_HOST         - Hostname (FQDN) clients use to reach CRDP. Defaults to
#                       'crdp.local' if not set. MUST be a hostname, not an IP
#                       (Kubernetes Ingress rejects IPs in the 'host:' field).

set -o pipefail

# ----- Parse flags -----
USE_MICROK8S=0
for arg in "$@"; do
    case "$arg" in
        --microk8s|-m)
            USE_MICROK8S=1
            ;;
        --help|-h)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument '$arg'. Use --help for usage." >&2
            exit 1
            ;;
    esac
done

# ----- Pre-flight: required tools -----
if ! command -v envsubst >/dev/null 2>&1; then
    echo "ERROR: envsubst is required but not installed." >&2
    echo "       On Debian/Ubuntu: sudo apt install gettext-base" >&2
    exit 1
fi

# Resolve the kubectl command once. By default the script calls plain
# 'kubectl'; with --microk8s it calls 'microk8s kubectl'. KUBECTL is
# intentionally unquoted at call sites so 'microk8s kubectl' splits into
# two argv tokens.
if [ "$USE_MICROK8S" -eq 1 ]; then
    if ! command -v microk8s >/dev/null 2>&1; then
        echo "ERROR: 'microk8s' not found on PATH (required for --microk8s)." >&2
        exit 1
    fi
    KUBECTL="microk8s kubectl"
else
    if ! command -v kubectl >/dev/null 2>&1; then
        echo "ERROR: 'kubectl' not found on PATH." >&2
        echo "       Re-run with --microk8s if targeting a MicroK8s install." >&2
        exit 1
    fi
    KUBECTL="kubectl"
fi
echo "Using kubectl command: $KUBECTL"

# Use sudo only when not already root.
if [ "$(id -u)" = "0" ]; then
    SUDO=""
else
    SUDO="sudo"
fi

# ----- REG_TOKEN_VALUE (silent prompt; it is a credential) -----
if [ -z "$REG_TOKEN_VALUE" ]; then
    read -rsp "Enter the CRDP App Registration Token from CipherTrust Manager: " REG_TOKEN_VALUE
    echo
    if [ -z "$REG_TOKEN_VALUE" ]; then
        echo "ERROR: No registration token provided. Aborting." >&2
        exit 1
    fi
    export REG_TOKEN_VALUE
fi

# ----- KEY_MANAGER_HOST (must be an IPv4 address) -----
# CRDP pods resolve KEY_MANAGER_HOST through cluster DNS (CoreDNS), which does
# not see the node's /etc/hosts. An FQDN that only resolves locally on the node
# will cause the pod to CrashLoopBackOff with "no such host". Require an IP.
is_ipv4() {
    local ip=$1 oct
    [[ $ip =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    for oct in "${BASH_REMATCH[@]:1}"; do
        (( oct <= 255 )) || return 1
    done
    return 0
}

if [ -n "$KEY_MANAGER_HOST" ] && ! is_ipv4 "$KEY_MANAGER_HOST"; then
    echo "KEY_MANAGER_HOST is set to '$KEY_MANAGER_HOST', which is not an IPv4 address."
    echo "  CRDP pods cannot resolve FQDNs from the node's /etc/hosts; an IP is required."
    KEY_MANAGER_HOST=""
fi

while [ -z "$KEY_MANAGER_HOST" ]; do
    read -rp "Enter the IPv4 address of the CipherTrust Manager (not an FQDN): " KEY_MANAGER_HOST
    if [ -z "$KEY_MANAGER_HOST" ]; then
        echo "ERROR: No CipherTrust Manager IP provided." >&2
        continue
    fi
    if ! is_ipv4 "$KEY_MANAGER_HOST"; then
        echo "ERROR: '$KEY_MANAGER_HOST' is not a valid IPv4 address. Enter an IP, not an FQDN." >&2
        KEY_MANAGER_HOST=""
    fi
done
export KEY_MANAGER_HOST

# ----- CRDP_HOST (default to crdp.local) -----
if [ -z "$CRDP_HOST" ]; then
    CRDP_HOST="crdp.local"
    echo "CRDP_HOST not set; using default: $CRDP_HOST"
    echo "  (override by exporting CRDP_HOST=<your-fqdn> before running)"
fi
export CRDP_HOST

# Detect this host's primary IP for the /etc/hosts mapping below.
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [ -z "$HOST_IP" ]; then
    echo "ERROR: Could not detect this host's primary IP (hostname -I returned nothing)." >&2
    echo "       Set the /etc/hosts mapping for $CRDP_HOST manually and re-run." >&2
    exit 1
fi

echo
echo "Using:"
echo "  KEY_MANAGER_HOST = $KEY_MANAGER_HOST"
echo "  CRDP_HOST        = $CRDP_HOST  (will map to $HOST_IP in /etc/hosts)"
echo

# ----- Ensure /etc/hosts maps $CRDP_HOST -> $HOST_IP on this host -----
# Find an existing mapping for $CRDP_HOST in /etc/hosts (skipping comment lines).
EXISTING_IP=$(awk -v h="$CRDP_HOST" '
    !/^[[:space:]]*#/ {
        for (i = 2; i <= NF; i++) {
            if ($i == h) { print $1; exit }
        }
    }
' /etc/hosts)

if [ -n "$EXISTING_IP" ]; then
    if [ "$EXISTING_IP" = "$HOST_IP" ]; then
        echo "/etc/hosts: $CRDP_HOST -> $HOST_IP already present."
    else
        echo "WARNING: /etc/hosts already maps $CRDP_HOST -> $EXISTING_IP (expected $HOST_IP)."
        echo "         Leaving existing entry alone. Edit /etc/hosts manually if it should change."
    fi
else
    echo "Adding '$HOST_IP $CRDP_HOST' to /etc/hosts (sudo may prompt)..."
    if ! echo "$HOST_IP $CRDP_HOST" | $SUDO tee -a /etc/hosts >/dev/null; then
        echo "ERROR: Failed to update /etc/hosts." >&2
        exit 1
    fi
    echo "/etc/hosts updated."
fi

# ----- Ensure the NGINX Ingress Controller is installed -----
# Detection: presence of the 'nginx' IngressClass is the authoritative signal.
NGINX_MANIFEST="https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/deploy/static/provider/baremetal/deploy.yaml"

if $KUBECTL get ingressclass nginx >/dev/null 2>&1; then
    echo "NGINX Ingress Controller is already installed (IngressClass 'nginx' present)."
else
    echo "NGINX Ingress Controller not found. Installing from the official manifest..."
    echo "  $NGINX_MANIFEST"
    if ! $KUBECTL apply -f "$NGINX_MANIFEST"; then
        echo "ERROR: kubectl apply failed for the NGINX manifest. Aborting." >&2
        exit 1
    fi

    # Wait for the controller Deployment to finish its initial rollout.
    # Using 'rollout status' instead of 'kubectl wait --for=ready pod' because
    # the pods may not exist immediately after the manifest is applied (kubectl
    # wait fails with "no matching resources found" in that brief gap).
    echo "Waiting for the NGINX controller Deployment to roll out (timeout 300s)..."
    if ! $KUBECTL rollout status deployment/ingress-nginx-controller \
            -n ingress-nginx --timeout=300s; then
        echo "ERROR: NGINX controller Deployment did not roll out within 300s. Aborting." >&2
        echo "       Investigate with: $KUBECTL get pods -n ingress-nginx" >&2
        exit 1
    fi

    # Patch the controller Deployment to use hostNetwork so it binds directly to
    # the node's port 80. Without this the controller listens only on a NodePort.
    echo "Patching NGINX controller Deployment to use hostNetwork=true..."
    if ! $KUBECTL patch deployment ingress-nginx-controller -n ingress-nginx \
            --type='json' \
            -p='[{"op":"add","path":"/spec/template/spec/hostNetwork","value":true},
                 {"op":"add","path":"/spec/template/spec/dnsPolicy","value":"ClusterFirstWithHostNet"}]'; then
        echo "ERROR: Failed to patch NGINX controller for hostNetwork. Aborting." >&2
        exit 1
    fi

    echo "Waiting for NGINX controller rollout after hostNetwork patch (timeout 180s)..."
    if ! $KUBECTL rollout status deployment/ingress-nginx-controller \
            -n ingress-nginx --timeout=180s; then
        echo "ERROR: NGINX controller rollout did not complete within 180s. Aborting." >&2
        exit 1
    fi

    # Final sanity check.
    if ! $KUBECTL get ingressclass nginx >/dev/null 2>&1; then
        echo "ERROR: NGINX install completed but IngressClass 'nginx' is still missing. Aborting." >&2
        exit 1
    fi

    echo "NGINX Ingress Controller installed and ready."
fi

# ----- Create / refresh the registration-token secret -----
$KUBECTL delete secret crdp-secret-name --ignore-not-found
$KUBECTL create secret generic crdp-secret-name --from-literal=regtoken="$REG_TOKEN_VALUE"

# ----- Apply the CRDP workload (Deployment + Service) -----
envsubst < crdp-app-svc-ing.yml | $KUBECTL apply -f -

# ----- Apply the Ingress -----
envsubst < crdp-ingress.yml | $KUBECTL apply -f -

# ----- Final summary for the operator -----
echo
echo "=============================================================="
echo "Deployment complete. CRDP is reachable at:"
echo "    http://$CRDP_HOST"
echo
echo "Use this value with the stress test:"
echo "    cd ../CRDP_Stress_App"
echo "    python3 CRDP_Stress.py -endpoint $CRDP_HOST -policy <name> -user <name>"
echo "=============================================================="
echo
echo "Notes:"
echo " - On THIS host, $CRDP_HOST -> $HOST_IP is set in /etc/hosts."
echo " - To call CRDP from OTHER hosts, add the same line to their /etc/hosts"
echo "   (or to your DNS):"
echo "     $HOST_IP $CRDP_HOST"
echo " - The CRDP Service is also exposed as a NodePort at <any-node-ip>:32085"
echo "   (bypass the Ingress entirely by commenting out the final"
echo "   'envsubst < crdp-ingress.yml' line above)."

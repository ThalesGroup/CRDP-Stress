#!/bin/bash
#
# Deploys CRDP to MicroK8s. The script:
#   1. Creates the crdp-secret-name Kubernetes secret from the CRDP App
#      Registration Token issued by CipherTrust Manager.
#   2. Applies the CRDP Deployment + Service (crdp-app-svc-ing.yml) after
#      substituting KEY_MANAGER_HOST.
#   3. Ensures the NGINX Ingress Controller is installed (installs it from the
#      official manifest if absent; aborts on any failure).
#   4. Ensures /etc/hosts on this host maps $CRDP_HOST to the host's primary IP.
#   5. Applies the Ingress (crdp-ingress.yml) after substituting CRDP_HOST.
#
# Environment variables consumed (the script prompts or defaults if unset):
#   REG_TOKEN_VALUE   - CRDP App Registration Token from CipherTrust Manager.
#                       Prompted for silently if not set.
#   KEY_MANAGER_HOST  - IP or FQDN of CipherTrust Manager. Prompted if not set.
#   CRDP_HOST         - Hostname (FQDN) clients use to reach CRDP. Defaults to
#                       'crdp.local' if not set. MUST be a hostname, not an IP
#                       (Kubernetes Ingress rejects IPs in the 'host:' field).

set -o pipefail

# ----- Pre-flight: required tools -----
if ! command -v envsubst >/dev/null 2>&1; then
    echo "ERROR: envsubst is required but not installed." >&2
    echo "       On Debian/Ubuntu: sudo apt install gettext-base" >&2
    exit 1
fi

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

# ----- KEY_MANAGER_HOST (echoed prompt; not sensitive) -----
if [ -z "$KEY_MANAGER_HOST" ]; then
    read -rp "Enter the IP address or FQDN of the CipherTrust Manager: " KEY_MANAGER_HOST
    if [ -z "$KEY_MANAGER_HOST" ]; then
        echo "ERROR: No CipherTrust Manager host provided. Aborting." >&2
        exit 1
    fi
    export KEY_MANAGER_HOST
fi

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

if microk8s kubectl get ingressclass nginx >/dev/null 2>&1; then
    echo "NGINX Ingress Controller is already installed (IngressClass 'nginx' present)."
else
    echo "NGINX Ingress Controller not found. Installing from the official manifest..."
    echo "  $NGINX_MANIFEST"
    if ! microk8s kubectl apply -f "$NGINX_MANIFEST"; then
        echo "ERROR: kubectl apply failed for the NGINX manifest. Aborting." >&2
        exit 1
    fi

    echo "Waiting for the NGINX controller pod to become ready (timeout 180s)..."
    if ! microk8s kubectl wait --namespace ingress-nginx \
            --for=condition=ready pod \
            --selector=app.kubernetes.io/component=controller \
            --timeout=180s; then
        echo "ERROR: NGINX controller pod did not become ready within 180s. Aborting." >&2
        exit 1
    fi

    # Patch the controller Deployment to use hostNetwork so it binds directly to
    # the node's port 80. Without this the controller listens only on a NodePort.
    echo "Patching NGINX controller Deployment to use hostNetwork=true..."
    if ! microk8s kubectl patch deployment ingress-nginx-controller -n ingress-nginx \
            --type='json' \
            -p='[{"op":"add","path":"/spec/template/spec/hostNetwork","value":true},
                 {"op":"add","path":"/spec/template/spec/dnsPolicy","value":"ClusterFirstWithHostNet"}]'; then
        echo "ERROR: Failed to patch NGINX controller for hostNetwork. Aborting." >&2
        exit 1
    fi

    echo "Waiting for NGINX controller rollout after hostNetwork patch (timeout 180s)..."
    if ! microk8s kubectl rollout status deployment/ingress-nginx-controller \
            -n ingress-nginx --timeout=180s; then
        echo "ERROR: NGINX controller rollout did not complete within 180s. Aborting." >&2
        exit 1
    fi

    # Final sanity check.
    if ! microk8s kubectl get ingressclass nginx >/dev/null 2>&1; then
        echo "ERROR: NGINX install completed but IngressClass 'nginx' is still missing. Aborting." >&2
        exit 1
    fi

    echo "NGINX Ingress Controller installed and ready."
fi

# ----- Create / refresh the registration-token secret -----
microk8s kubectl delete secret crdp-secret-name --ignore-not-found
microk8s kubectl create secret generic crdp-secret-name --from-literal=regtoken="$REG_TOKEN_VALUE"

# ----- Apply the CRDP workload (Deployment + Service) -----
envsubst < crdp-app-svc-ing.yml | microk8s kubectl apply -f -

# ----- Apply the Ingress -----
envsubst < crdp-ingress.yml | microk8s kubectl apply -f -

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

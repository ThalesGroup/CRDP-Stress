#!/bin/bash
#
# Deploys CRDP to MicroK8s:
#   1. Creates the crdp-secret-name Kubernetes secret from the CRDP App
#      Registration Token issued by CipherTrust Manager.
#   2. Applies the CRDP Deployment + Service (crdp-app-svc-ing.yml) after
#      substituting KEY_MANAGER_HOST.
#   3. Ensures the NGINX Ingress Controller is running with hostNetwork=true.
#   4. Applies the Ingress (crdp-ingress.yml) after substituting CRDP_HOST.
#
# Environment variables consumed (the script prompts or auto-detects if unset):
#   REG_TOKEN_VALUE   - CRDP App Registration Token from CipherTrust Manager.
#                       Prompted for (silently) if not set.
#   KEY_MANAGER_HOST  - IP or FQDN of the CipherTrust Manager.
#                       Prompted for if not set.
#   CRDP_HOST         - Hostname/IP clients use to reach CRDP (lands in the
#                       Ingress 'host:' field). Auto-detected from the local
#                       machine's primary IP if not set.

# envsubst (from gettext) is required for variable substitution into the YAMLs.
if ! command -v envsubst >/dev/null 2>&1; then
    echo "ERROR: envsubst is required but not installed." >&2
    echo "       On Debian/Ubuntu: sudo apt install gettext-base" >&2
    exit 1
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

# ----- CRDP_HOST (auto-detect from this machine's primary IP) -----
if [ -z "$CRDP_HOST" ]; then
    CRDP_HOST=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -z "$CRDP_HOST" ]; then
        echo "ERROR: Could not auto-detect a host IP for CRDP_HOST." >&2
        echo "       Set CRDP_HOST manually (export CRDP_HOST=<ip-or-fqdn>) and re-run." >&2
        exit 1
    fi
    echo "Auto-detected CRDP_HOST=$CRDP_HOST  (override by exporting CRDP_HOST before running)"
    export CRDP_HOST
fi

echo
echo "Using:"
echo "  KEY_MANAGER_HOST = $KEY_MANAGER_HOST"
echo "  CRDP_HOST        = $CRDP_HOST"
echo

# Delete any existing secret, then (re)create it from the registration token.
microk8s kubectl delete secret crdp-secret-name --ignore-not-found
microk8s kubectl create secret generic crdp-secret-name --from-literal=regtoken="$REG_TOKEN_VALUE"

# Apply the CRDP Deployment + Service, substituting KEY_MANAGER_HOST.
envsubst < crdp-app-svc-ing.yml | microk8s kubectl apply -f -

# Ensure the NGINX Ingress Controller is deployed before applying the Ingress resource.
# The MicroK8s ingress addon installs a DaemonSet named "nginx-ingress-microk8s-controller"
# in the "ingress" namespace; use its presence as the readiness signal.
if microk8s kubectl get daemonset nginx-ingress-microk8s-controller -n ingress >/dev/null 2>&1; then
    echo "NGINX Ingress Controller is already deployed."
else
    echo "NGINX Ingress Controller not found. Enabling MicroK8s ingress addon..."
    microk8s enable ingress
    echo "Waiting for Ingress Controller pods to become ready..."
    microk8s kubectl rollout status daemonset/nginx-ingress-microk8s-controller -n ingress --timeout=120s
fi

# On some MicroK8s versions (observed on v1.33.9) the ingress addon does NOT set
# hostNetwork=true on the controller DaemonSet, so the controller never binds to the
# node's physical interface on ports 80/443 and is unreachable from outside the cluster.
# Detect that case and patch the DaemonSet so the controller listens directly on each
# node's IP. Safe to re-run: patching when hostNetwork is already true is a no-op.
HOST_NETWORK=$(microk8s kubectl get daemonset nginx-ingress-microk8s-controller -n ingress \
    -o jsonpath='{.spec.template.spec.hostNetwork}' 2>/dev/null)
if [ "$HOST_NETWORK" != "true" ]; then
    echo "Patching Ingress Controller DaemonSet to use hostNetwork=true..."
    microk8s kubectl patch daemonset nginx-ingress-microk8s-controller -n ingress \
        --type='json' \
        -p='[{"op":"add","path":"/spec/template/spec/hostNetwork","value":true},
             {"op":"add","path":"/spec/template/spec/dnsPolicy","value":"ClusterFirstWithHostNet"}]'
    echo "Waiting for Ingress Controller rollout after hostNetwork patch..."
    microk8s kubectl rollout status daemonset/nginx-ingress-microk8s-controller -n ingress --timeout=120s
else
    echo "Ingress Controller already running with hostNetwork=true."
fi

# Apply the Ingress resource for load-balanced host-based routing.
envsubst < crdp-ingress.yml | microk8s kubectl apply -f -

# ----- Final note for the operator -----
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
echo " - The CRDP Service is also exposed as a NodePort at <any-node-ip>:32085"
echo "   (skip the Ingress entirely by commenting out the final 'envsubst < crdp-ingress.yml' line)."
echo " - Clients reaching CRDP via the Ingress must send Host: $CRDP_HOST."
echo "   If CRDP_HOST is an FQDN, map it to a node IP in DNS or /etc/hosts on the client."
echo " - For round-robin client distribution across multiple ingress nodes, map several"
echo "   node IPs to the same hostname (DNS or /etc/hosts)."

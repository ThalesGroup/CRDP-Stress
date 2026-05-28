#!/bin/bash
#
# Tears down the CRDP resources created by makeSecretandDeploy.sh and verifies
# the cleanup. Removes (from the default namespace):
#   - Ingress     crdp-ingress
#   - Deployment  crdp-deployment   (also terminates its pods)
#   - Service     crdp-service
#   - Secret      crdp-secret-name
#
# Intentionally LEFT in place (the deploy script is idempotent and will detect
# or skip them on its next run):
#   - NGINX Ingress Controller (in the ingress-nginx namespace)
#   - The /etc/hosts entry for $CRDP_HOST
#   - MicroK8s itself, Calico CNI, CoreDNS
#
# Exits 0 if every CRDP resource is verified gone; exits 1 otherwise.

set -o pipefail

echo "Deleting CRDP resources from the default namespace..."
microk8s kubectl delete ingress    crdp-ingress     --ignore-not-found
microk8s kubectl delete deployment crdp-deployment  --ignore-not-found
microk8s kubectl delete service    crdp-service     --ignore-not-found
microk8s kubectl delete secret     crdp-secret-name --ignore-not-found

# Pods are owned by the Deployment; they enter Terminating state when the
# Deployment is removed and usually disappear within a few seconds. Wait briefly
# so the verification step reflects steady state.
echo
echo "Waiting for any CRDP pods to terminate (up to 60s)..."
microk8s kubectl wait --for=delete pod -l run=crdp --timeout=60s 2>/dev/null || true

echo
echo "Verifying cleanup..."

failures=0

check() {
    # $1 = resource kind, $2 = resource name
    if microk8s kubectl get "$1" "$2" >/dev/null 2>&1; then
        echo "  FAIL: $1/$2 still exists"
        failures=$((failures + 1))
    else
        echo "  OK:   $1/$2 removed"
    fi
}

check ingress    crdp-ingress
check deployment crdp-deployment
check service    crdp-service
check secret     crdp-secret-name

# Pods carry the label 'run=crdp' (the Deployment selector); confirm none remain.
remaining_pods=$(microk8s kubectl get pods -l run=crdp -o name 2>/dev/null | wc -l)
if [ "$remaining_pods" -eq 0 ]; then
    echo "  OK:   no CRDP pods remain"
else
    echo "  FAIL: $remaining_pods CRDP pod(s) still present:"
    microk8s kubectl get pods -l run=crdp
    failures=$((failures + 1))
fi

echo
echo "=============================================================="
if [ "$failures" -eq 0 ]; then
    echo "CRDP cleanup complete. You can re-run ./makeSecretandDeploy.sh"
    echo "=============================================================="
    exit 0
else
    echo "WARNING: $failures CRDP resource(s) still present."
    echo "Inspect with:  microk8s kubectl get all,ingress,secret -A | grep crdp"
    echo "=============================================================="
    exit 1
fi

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
# Flags:
#   --microk8s, -m      Use 'microk8s kubectl' instead of plain 'kubectl' for
#                       every cluster operation. Use this when targeting a
#                       MicroK8s installation.
#   --help, -h          Show usage and exit.
#
# Exits 0 if every CRDP resource is verified gone; exits 1 otherwise.

set -o pipefail

# ----- Parse flags -----
USE_MICROK8S=0
for arg in "$@"; do
    case "$arg" in
        --microk8s|-m)
            USE_MICROK8S=1
            ;;
        --help|-h)
            sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument '$arg'. Use --help for usage." >&2
            exit 1
            ;;
    esac
done

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

echo "Deleting CRDP resources from the default namespace..."
$KUBECTL delete ingress    crdp-ingress     --ignore-not-found
$KUBECTL delete deployment crdp-deployment  --ignore-not-found
$KUBECTL delete service    crdp-service     --ignore-not-found
$KUBECTL delete secret     crdp-secret-name --ignore-not-found

# Pods are owned by the Deployment; they enter Terminating state when the
# Deployment is removed and usually disappear within a few seconds. Wait briefly
# so the verification step reflects steady state.
echo
echo "Waiting for any CRDP pods to terminate (up to 60s)..."
$KUBECTL wait --for=delete pod -l run=crdp --timeout=60s 2>/dev/null || true

echo
echo "Verifying cleanup..."

failures=0

check() {
    # $1 = resource kind, $2 = resource name
    if $KUBECTL get "$1" "$2" >/dev/null 2>&1; then
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
remaining_pods=$($KUBECTL get pods -l run=crdp -o name 2>/dev/null | wc -l)
if [ "$remaining_pods" -eq 0 ]; then
    echo "  OK:   no CRDP pods remain"
else
    echo "  FAIL: $remaining_pods CRDP pod(s) still present:"
    $KUBECTL get pods -l run=crdp
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
    echo "Inspect with:  $KUBECTL get all,ingress,secret -A | grep crdp"
    echo "=============================================================="
    exit 1
fi

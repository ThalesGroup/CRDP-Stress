#!/bin/bash
#
# Small script file that derives a crdp-secret-name from the CRDP App Registration Token on CM
# and saves it under the environmental variable "crdp-secret-name". Once defined, the Kubernetes
# workload is deployed from crdp-app-svc-ing.yml and load-balanced host-based routing is
# deployed from crdp-ingress.yml (the default configuration).

# Replace the regtoken value below with the one from CM for the CRDP App
export REG_TOKEN_VALUE=v8Eg0jXcq3mGNkFHlvDnavAGFBO5qY7P4WfZmiAD5MOtR90NmRzrpgUL4OrKxlFf

# delete any existing value for crdp-secret-name
microk8s kubectl delete secret crdp-secret-name

# create (or recreate) secret
microk8s kubectl create secret generic crdp-secret-name --from-literal=regtoken=$REG_TOKEN_VALUE

# Deploy Kubernetes workload (Deployment + Service)
microk8s kubectl apply -f crdp-app-svc-ing.yml

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

# Deploy Ingress resource for load-balanced host-based routing (default configuration)
microk8s kubectl apply -f crdp-ingress.yml

# ============================================================================
# Default Configuration: Load-Balanced Ingress (NGINX Ingress Controller)
# ============================================================================
# The default deployment exposes CRDP via host-based routing at crdp.test256.io using
# the NGINX Ingress Controller. MetalLB is NOT required. Note that the MicroK8s ingress
# addon does NOT set hostNetwork=true on the controller DaemonSet by default (observed
# on MicroK8s v1.33.9), so the script above patches the DaemonSet to enable hostNetwork.
# Once patched, the controller binds directly to ports 80/443 on each node's physical
# IP, and NGINX load-balances requests across all CRDP backend pods.
#
# The script above automatically:
#   - Enables the MicroK8s ingress addon if it is not already deployed
#   - Patches the controller DaemonSet to use hostNetwork=true if it is not already set
#   - Waits for the rollout to complete before applying the Ingress resource
# No manual ingress-addon setup is required.
#
# Manual prerequisite:
#
#   Map crdp.test256.io to one or more of your MicroK8s node IPs in DNS or /etc/hosts
#   on every client that will call CRDP. In this environment the node IPs are:
#     192.168.1.187  (sphere)
#     192.168.1.188  (kube)
#   For a single-node mapping:
#     192.168.1.188  crdp.test256.io
#   For round-robin DNS across both nodes (distributes client connections between both
#   ingress controller pods for Layer-1 load balancing), add both entries under the
#   same hostname in DNS, or add both lines to /etc/hosts:
#     192.168.1.188  crdp.test256.io
#     192.168.1.187  crdp.test256.io
#   NGINX will always load-balance across all CRDP backend pods regardless of which
#   node the request enters on.
#
# ============================================================================
# Alternative: NodePort-Only Access (no Ingress)
# ============================================================================
# If you do not want (or cannot enable) the NGINX Ingress Controller, you can reach
# CRDP directly via the NodePort service defined in crdp-app-svc-ing.yml at:
#        http://<any-node-ip>:32085
# To use this path, comment out the "kubectl apply -f crdp-ingress.yml" line above.

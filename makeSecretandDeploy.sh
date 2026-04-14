#!/bin/bash
#
# Small script file that derives a crdp-secret-name from the CRDP App Registration Token on CM
# and saves it under the environmental variable "crdp-secret-name". Once defined, the Kubernetes
# workload is deployed from crdp-app-svc-ing.yml and load-balanced host-based routing is
# deployed from crdp-ingress.yml (the default configuration).

# Replace the regtoken value below with the one from CM for the CRDP App
export REG_TOKEN_VALUE=wauGqIdZxTgNTXAeaG6xOKewmPzFLTgo1JaFcvuSPruZi8ELi1MIR2K5uMrhivya

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

# Deploy Ingress resource for load-balanced host-based routing (default configuration)
microk8s kubectl apply -f crdp-ingress.yml

# ============================================================================
# Default Configuration: Load-Balanced Ingress (NGINX Ingress Controller)
# ============================================================================
# The default deployment exposes CRDP via host-based routing at crdp.test256.io using
# the NGINX Ingress Controller. MetalLB is NOT required: the MicroK8s ingress addon
# deploys ingress-nginx as a DaemonSet with hostNetwork=true, so the controller listens
# on ports 80/443 of every node's IP directly, giving you multi-node load balancing.
#
# The script above automatically checks whether the NGINX Ingress Controller is deployed
# and runs "microk8s enable ingress" if it is not, so no manual ingress-addon setup is
# required.
#
# Manual prerequisite:
#
#   Map crdp.test256.io to one or more node IP addresses.
#   Because the controller runs on hostNetwork, any node IP will work. Add an entry in
#   DNS or /etc/hosts. Node IPs in this environment typically fall in the
#   192.168.3.100-192.168.3.250 range. For example:
#     192.168.3.100  crdp.test256.io
#   For high availability, point DNS at multiple node IPs in that range or put an
#   external load balancer / round-robin DNS in front of the nodes.
#
# ============================================================================
# Alternative: NodePort-Only Access (no Ingress)
# ============================================================================
# If you do not want (or cannot enable) the NGINX Ingress Controller, you can reach
# CRDP directly via the NodePort service defined in crdp-app-svc-ing.yml at:
#        http://<any-node-ip>:32085
# To use this path, comment out the "kubectl apply -f crdp-ingress.yml" line above.

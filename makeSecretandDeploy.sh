#!/bin/bash
#
# Small script file that defines derives a crdp-secret-name from the CRDP App Registration Token on CM and saves it under the
# environmental variable "crdp-secret-name". Once it is define, then the kubernetes cluster is deployed as defined
# in crdp-app-svc.ing.yml

# Replace the regtoken value below with the one from CM for the CRDP App
export REG_TOKEN_VALUE=wauGqIdZxTgNTXAeaG6xOKewmPzFLTgo1JaFcvuSPruZi8ELi1MIR2K5uMrhivya

# delete any existing value for crdp-secret-name
microk8s kubectl delete secret crdp-secret-name

# create (or recreate) secret
microk8s kubectl create secret generic crdp-secret-name --from-literal=regtoken=$REG_TOKEN_VALUE

# Deploy Kubernetes Cluster
microk8s kubectl apply -f crdp-app-svc-ing.yml

# ============================================================================
# MetalLB + Ingress Option
# ============================================================================
# If you want to use host-based routing (e.g., crdp.test256.io) with an Ingress resource,
# you must have both MetalLB and the NGINX Ingress Controller deployed and configured.
# Without MetalLB, the Ingress Controller has no external IP and is unreachable from
# outside the cluster.
#
# Prerequisites:
#
#   1. Enable MetalLB on MicroK8s:
#        microk8s enable metallb
#      You will be prompted for an IP address range. Provide a range of unused IPs on your
#      local network that MetalLB can assign to LoadBalancer services. For example:
#        microk8s enable metallb:192.168.3.100-192.168.3.250
#
#   2. Verify MetalLB is running:
#        microk8s kubectl get pods -n metallb-system
#
#   3. Enable the NGINX Ingress Controller:
#        microk8s enable ingress
#
#   4. Verify the Ingress Controller has been assigned an external IP by MetalLB:
#        microk8s kubectl get svc -n ingress
#      The EXTERNAL-IP column should show an IP from the MetalLB range (not <pending>).
#      For example, MetalLB may assign 192.168.3.100 to the Ingress Controller.
#
#   5. Map crdp.test256.io to the Ingress Controller's assigned external IP.
#      Add an entry in DNS or /etc/hosts using the IP from step 4. For example:
#        192.168.3.100  crdp.test256.io
#
# Once all prerequisites are met, uncomment the following line to deploy the Ingress resource:
# microk8s kubectl apply -f crdp-ingress-metallb.yml

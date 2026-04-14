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
# Ingress Option (NGINX Ingress Controller)
# ============================================================================
# If you want to use host-based routing (e.g., crdp.test256.io) with an Ingress resource,
# you need the NGINX Ingress Controller enabled in MicroK8s. MetalLB is NOT required:
# the MicroK8s ingress addon deploys ingress-nginx as a DaemonSet with hostNetwork=true,
# so the controller listens on ports 80/443 of every node's IP directly.
#
# Prerequisites:
#
#   1. Enable the NGINX Ingress Controller:
#        microk8s enable ingress
#
#   2. Verify the Ingress Controller pods are running on every node:
#        microk8s kubectl get pods -n ingress -o wide
#      You should see one nginx-ingress-microk8s-controller pod per node, all in Running state.
#
#   3. Map crdp.test256.io to any node's IP address.
#      Because the controller runs on hostNetwork, any node IP will work. Add an entry
#      in DNS or /etc/hosts. Node IPs in this environment typically fall in the
#      192.168.3.100-192.168.3.250 range. For example:
#        192.168.3.100  crdp.test256.io
#      For high availability, point DNS at multiple node IPs in that range or put an
#      external load balancer / round-robin DNS in front of the nodes.
#
# Once all prerequisites are met, uncomment the following line to deploy the Ingress resource:
# microk8s kubectl apply -f crdp-ingress.yml

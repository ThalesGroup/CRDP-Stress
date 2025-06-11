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

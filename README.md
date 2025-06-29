**Welcome to a python-based CRDP stressing utility**

It works fairly simply.  It create random plaintext data and then submits that to CRDP to determine how long CRDP takes to protect (encrypt) or reveal (decrypt).

Command line parameters allow the user to specify the number of times to repeat the encryption process as well as whether the protection process is record-by-record or 
as a bulk submission (which alwasy performs faster).

Usage:
**py CRDP_Stress.py [-h] -e HOSTNAMECRDP -p PROTECTIONPOLICY -b BATCHSIZE -u USERNAME [-bulk] [-c {ALPHANUMERIC, DIGITSONLY, PRINTABLEASCII}] [-f FILENAME]** where:

-e HOSTNAME         - The host name (or IP address) and port (optional) where CRDP is hosted.  E.g., cm-netptune.test256.io:8090

-p PROTECTIONPOLICY - The name of the Protection Policy that has been defined in CRDP. E.g., CRDP-DP-Policy1

-b BATCHSIZE        - How many times the protection / reveal action should be be performed during the test.  
                        This is an integer between 1 and 1,000,000.
                        Note that NON-bulk testing can take a LONG TIME with large bulk sizes.
                   
-u USERNAME         - The name of the user that will be used during the REVEAL test

-charSet   (optional)
            NEITHER - Read and Copy Keys and Secrets From Source to Destination
            SOURCE - Only Read and List Keys and Secrets on Source Server.  No reads from or writes to Destination Server
            DESTINATION - Only Read and List Objects on Destination Server.  No reads from Source Server
            BOTH - Only Read and List Keys, Secrets, or Objects on Source and Destination Server.  No writes are made to Destination Server

[-bulk]          - just a FLAG that indicates whether the test should be formed as a bulk submission

FILENAME         - If you want to supply an actual file for encryption and descryption, you can add it here (text or binary).  
                   If a file is supplied, then the BULK flag is automatically set and the BATCHSIZE is ignored.


**Additional File Information:**

For fun, I have included a file called *attack.sh*.  This is a linux bash file that will call CRDP-Stress 10 times so that your 
CRDP environment is required to process multiple requests concurrently.  This is useful if you have established a Kubernetes
Cluster with multiple pods of CRDP running on multiple hosts.

Furthermore, I have also included a file called *crdp-app-svc-ing.yml* which will establish a Kubernetes cluster of CRDP pods.  To use this file, you will need to use MicroK8s or some Kubernetes controller and have defined a *crdp-secret-name* prior to deploying the yml file.

The **makeSecretandDeploy.sh** script will do this for you (but you must still edit it and update the RegToken).  It executes the following steps:

1) Define a secret, by using the command:
**microk8s kubectl create secret generic crdp-secret-name --from-literal=regtoken=myCRDPAppregistrationtokenfromCipherTrust**

2) Deploy the environment by the following command:
**mkcrok8s kubectl apply -f crdp-app-svc-ing.yml**







**Welcome to a python-based CRDP stressing utility**

It works fairly simply.  It create random plaintext data and then submits that to CRDP to determine how long CRDP takes to protect (encrypt) or reveal (decrypt).

Command line parameters allow the user to specify the number of times to repeat the encryption process as well as whether the protection process is record-by-record or 
as a bulk submission (which alwasy performs faster).

Usage:
**py CRDP-Stress.py [-h] -e HOSTNAMECRDP -p PROTECTIONPOLICY -b BATCHSIZE -u USERNAME [-bulk] [-f FILENAME]** where:

HOSTNAME         - The host name (or IP address) and port (optional) where CRDP is hosted.  E.g., cm-netptune.test256.io:8090

PROTECTIONPOLICY - The name of the Protection Policy that has been defined in CRDP. E.g., CRDP-DP-Policy1

BATCHSIZE        - How many times the protection / reveal action should be be performed during the test.  This is an integer between 1 and 1,000,000.
                   Note that NON-bulk testing can take a LONG TIME with large bulk sizes.
                   
USERNAME         - The name of the user that will be used during the REVEAL test

[-bulk]          - just a FLAG that indicates whether the test should be formed as a bulk submission

FILENAME         - If you want to supply an actual file for encryption and descryption, you can add it here (text or binary).  
                   If a file is supplied, then the BULK flag is automatically set and the BATCHSIZE is ignored.


# CRDP Stress
#
# Opens a CSV file and then submits data in second, to N fields to a CRDP
# instance.  Since CRDP handles batch encryption, the data read from the CSV
# file will be organized into batches before being submitted to the CRDP instance.
#
# Time will be measured for the file to be encrypted excluding file I/O actions.
#
# Usage:  CRDP_Stress.py
#           -e <CRDP endpoint hostname or IP address>
#           -i <input csv file>
#           -b <batch size>
#           -o <output filename>
#           -h - header flag.  indicates if header is present in input csv file
#
import argparse
import time
from CRDP_REST_API import *
import random
from tqdm import tqdm
from termcolor import colored
import os
import base64


#  --------- DEFINED Routines -----------------
def getRNDStr(t_len):
    # Simple routine that generates a randon plaintext payload

    return "".join(
        random.choices(
            "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", k=t_len
        )
    )
#

#####################################################################
# Code for collecting and parsing input information from command line
#####################################################################
parser = argparse.ArgumentParser()
parser.add_argument("-e", nargs=1, action="store", required=True, dest="hostnameCRDP")
parser.add_argument(
    "-p", nargs=1, action="store", required=True, dest="protectionPolicy"
)
parser.add_argument(
    "-b", nargs=1, action="store", required=True, dest="batchSize", type=int
)
parser.add_argument("-u", nargs=1, action="store", required=True, dest="username")
parser.add_argument("-bulk", action=argparse.BooleanOptionalAction)

parser.add_argument(
    "-f", nargs=1, action="store", required=False, dest="inFile", type=argparse.FileType('r'))

args = parser.parse_args()


tmpStr = "\nCDRP Stress starting... LET THE STRESS BEGIN!"
print(colored(tmpStr, "white", attrs=["underline"]))

# Echo Input Parameters
hostCRDP = args.hostnameCRDP[0]
batchSize = args.batchSize[0]
protectionPolicy = args.protectionPolicy[0]
bulkFlag = False
if args.bulk:
    bulkFlag = True
r_user = args.username[0]

# Special processing if file is supplied.  Specifically, if it is provided
# we need to a) ensure the file exists and b) we process it as BULK
inFile = ""
if args.inFile:
    inFile = str(args.inFile[0].name)

    if os.path.isfile(inFile):
        bulkFlag = True

#####################################################################
# Echo back input information for validation
#####################################################################
print(" Input Parameters:")

# include filename if it is specified
if len(inFile) > 0:
    tmpStr = (
        "  CRDPHost: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n  Input File: %s\n"
        % (hostCRDP, protectionPolicy, bulkFlag, inFile)
    )
else:
    tmpStr = (
        "  CRDPHost: %s\n  BatchSize: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n"
        % (hostCRDP, batchSize, protectionPolicy, bulkFlag)
    )

print(tmpStr)

# Get a string of 64 random characters and treat is as cleartext (plaintext)
p_data = getRNDStr(64)

# Reserve some variables for later use
p_data_array = []  # reserve for later use - cleartext (plaintext)
c_data = []  # reserve for later use - protectedtext
c_data_array = []  # reserve for later use - protectedtext
c_version = []  # reserve for later use - cipher version
r_data = []  # reserve for later use - revealedtext
r_data_array = []  # reserve for later use - revealtext

# how many times do we want to encrypt it?
p_count = batchSize
data_size = len(p_data)*p_count

#####################################################################
# Let's encrypt the data as fast as we can in two ways:
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP PROTECTION Test Started ***", "white", attrs=["bold"]))
print(" --> Start time: ", starttime)

# Process as bulk if specified as such or if file is specified
if bulkFlag == False:
    # time - re-retrieve start time
    starttime = time.time()

    for i in tqdm(range(p_count), desc="Discrete PROTECT Progress"):
        c_data, c_version = protectData(hostCRDP, p_data, protectionPolicy)

else:
    # build plaintext array for processing unless file is specified
    if len(inFile) > 0:
        with open(inFile, 'rb') as f:
            # read in file content but only keep ascii characters
            f_content = f.read()            
            f_ascii = f_content.decode('ascii', 'ignore')
            data_size = len(f_ascii)

            # once you have ascii characters, add them to the
            # plaintext array
            p_data_array.clear()
            p_data_array.append(f_ascii)

    else:
        for i in range(p_count):
            p_data_array.append(p_data)

    p_data = p_data_array[0]
    print(" -->  CRDP Bulk PROTECT processing...")
    # time - re-retrieve start time
    starttime = time.time()
    c_data_array, c_version = protectBulkData(hostCRDP, p_data_array, protectionPolicy)
    c_data = c_data_array[0][
        CRDP_PROTECTED_DATA_NAME
    ]  # retreive first recorded in returned data


# time - get end time
endtime = time.time()
print(" -->   End time: ", endtime)

deltatimesec = endtime - starttime
pRate = data_size / deltatimesec

outStr = (
    "\nCRDP Test Completed - PROTECT. %s plaintext bytes processed. Process time: %5.2f sec.  Rate: %5.2f Bps.\n"
    % (data_size, deltatimesec, pRate)
)
print(colored(outStr, "light_green", attrs=["bold"]))


#####################################################################
# Let's decrypt the data as fast as we can in two ways:
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP REVEAL Test Started ***", "white", attrs=["bold"]))
print(" --> Start time: ", starttime)

if bulkFlag == False:
    # time - re-retrieve start time
    starttime = time.time()

    for i in tqdm(range(p_count), desc="Discrete REVEAL Progress"):
        r_data = revealData(hostCRDP, c_data, protectionPolicy, c_version, r_user)

else:
    # no need to rebuild c_data_array since it was populated earlier

    print(" -->  CRDP Bulk REVEAL processing...")

    # time - re-retrieve start time
    starttime = time.time()
    r_data_array = revealBulkData(
        hostCRDP, c_data_array, protectionPolicy, c_version, r_user
    )
    r_data = r_data_array[0][CRDP_DATA_NAME]  # retreive first recorded in returned data


# time - get end time
endtime = time.time()
print(" -->   End time: ", endtime)

deltatimesec = endtime - starttime
pRate = data_size / deltatimesec

outStr = (
    "\nCRDP Test Completed - REVEAL. %s ciphertrext bytes processed. Process time: %5.2f sec.  Rate: %5.2f Bps.\n"
    % (data_size, deltatimesec, pRate)
)
print(colored(outStr, "light_green", attrs=["bold"]))

outstr = "Plaintext (PT), CipherText (CT), and RevealText (RT) are as follows:"
print(outstr)

outStr = " PT: %s\n CT: %s\n RT: %s" % (p_data[0:63], c_data[0:63], r_data[0:63])
print(colored(outStr, "light_grey"))

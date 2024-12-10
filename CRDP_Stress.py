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
import sys
import csv
import argparse
import time
import os.path
from os import path
from CRDP_REST_API import *
import random
from tqdm import tqdm
from termcolor import colored

#  --------- DEFINED Routines -----------------
def getRNDStr(t_len):
    # Simple routine that generates a randon plaintext payload

    return ''.join(random.choices("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", k=t_len))
#

#####################################################################
# Code for collecting and parsing input information from command line
#####################################################################
parser = argparse.ArgumentParser()
parser.add_argument('-e', nargs=1, action='store', required=True, dest='hostnameCRDP')
parser.add_argument('-p', nargs=1, action='store', required=True, dest='protectionPolicy')
parser.add_argument('-b', nargs=1, action='store', required=True, dest='batchSize', type=int)
parser.add_argument('-u', nargs=1, action='store', required=True, dest='username')
parser.add_argument('-bulk', action=argparse.BooleanOptionalAction)

args = parser.parse_args()


tmpStr = "\nCDRP Stress starting... LET THE STRESS BEGIN!"
print(colored(tmpStr, 'white', attrs=["underline"]))

# Echo Input Parameters
hostCRDP            = args.hostnameCRDP[0]
batchSize           = args.batchSize[0]
protectionPolicy    = args.protectionPolicy[0]
bulkFlag            = False
if args.bulk:
    bulkFlag = True
r_user              = args.username[0]

#####################################################################
# Echo back input information for validation
#####################################################################
print(" Input Parameters:")
tmpStr = "  CRDPHost: %s\n  BatchSize: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n" %(hostCRDP, batchSize, protectionPolicy, bulkFlag)
print(tmpStr)

# Get a string of 64 random characters.
c_data          = getRNDStr(48)

# Reserve some variables for later use
c_data_array    = [] #reserve for later use
p_data          = [] #reserve for later use
p_data_array    = [] #reserve for later use
p_version       = [] #reserve for later use
r_data          = [] #reserve for later use
r_data_array    = [] #reserve for later use

# how many times do we want to encrypt it?
c_count = batchSize

#####################################################################
# Let's encrypt the data as fast as we can in two ways:  
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP PROTECTION Test Started ***", 'white', attrs=["bold"]))
print(" --> Start time: ", starttime)

if bulkFlag == False:
    # time - re-retrieve start time
    starttime = time.time()
    
    for i in tqdm(range(c_count), desc="Discrete PROTECT Progress"):
        p_data, p_version = protectData(hostCRDP, c_data, protectionPolicy)

else:
    for i in tqdm(range(c_count), desc="Bulk PROTECT Prep Progress"):
        c_data_array.append(c_data)

    print(" -->  CRDP Bulk PROTECT processing...")
    # time - re-retrieve start time
    starttime = time.time()
    p_data_array, p_version = protectBulkData(hostCRDP, c_data_array, protectionPolicy)
    p_data = p_data_array[0][CRDP_PROTECTED_DATA_NAME]  # retreive first recorded in returned data


# time - get end time
endtime = time.time()
print(" -->   End time: ", endtime)

deltatimesec = (endtime-starttime)
pRate = c_count/deltatimesec

outStr = "\n* CRDP Test Completed - PROTECT. %s plaintext strings processed. Process time: %5.2f sec.  Rate: %5.2f tps.\n" %(c_count, deltatimesec, pRate) 
print(colored(outStr, 'light_green', attrs=["bold"]))


#####################################################################
# Let's decrypt the data as fast as we can in two ways:  
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP REVEAL Test Started ***", 'white', attrs=["bold"]))
print(" --> Start time: ", starttime)

if bulkFlag == False:
    # time - re-retrieve start time
    starttime = time.time()
    
    for i in tqdm(range(c_count), desc="Discrete REVEAL Progress"):
        r_data = revealData(hostCRDP, p_data, protectionPolicy, p_version, r_user)

else:
    for i in tqdm(range(c_count), desc="Bulk REVEAL Prep Progress"):
        c_data_array.append(c_data)

    print(" -->  CRDP Bulk REVEAL processing...")

    # time - re-retrieve start time
    starttime = time.time()
    r_data_array = revealBulkData(hostCRDP, p_data_array, protectionPolicy, p_version, r_user)
    r_data = r_data_array[0][CRDP_DATA_NAME]  # retreive first recorded in returned data
    

# time - get end time
endtime = time.time()
print(" -->   End time: ", endtime)

deltatimesec = (endtime-starttime)
pRate = c_count/deltatimesec

outStr = "\n* CRDP Test Completed - REVEAL. %s ciphertrext strings processed. Process time: %5.2f sec.  Rate: %5.2f tps.\n" %(c_count, deltatimesec, pRate) 
print(colored(outStr, 'light_green', attrs=["bold"]))

outStr = "    PT: %s\n    CT: %s\n    RT: %s" %(c_data, p_data, r_data) 
print(outStr)




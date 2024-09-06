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

# 
def getRNDStr(t_len):
    return ''.join(random.choices("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ", k=t_len))
#

parser = argparse.ArgumentParser()
parser.add_argument('-e', nargs=1, action='store', required=True, dest='hostnameCRDP')
parser.add_argument('-p', nargs=1, action='store', required=True, dest='protectionPolicy')
parser.add_argument('-b', nargs=1, action='store', required=True, dest='batchSize', type=int)
parser.add_argument('-bulk', action=argparse.BooleanOptionalAction)

args = parser.parse_args()

tmpStr = "\nCDRP Stress starting..."
print(tmpStr)

# Echo Input Parameters
hostCRDP            = args.hostnameCRDP[0]
batchSize           = args.batchSize[0]
protectionPolicy    = args.protectionPolicy[0]
bulkFlag            = False
if args.bulk:
    bulkFlag = True

print(" Input Parameters:")
tmpStr = "  CRDPHost: %s\n  BatchSize: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n" %(hostCRDP, batchSize, protectionPolicy, bulkFlag)
print(tmpStr)

# Get a string of 64 characters.  Random, if possible
c_data = getRNDStr(64)

# how many times do we want to encrypt it?
c_count = batchSize

# time - get start time
starttime = time.time()
print("Start time: ", starttime)

if bulkFlag == False:
    # time - re-retrieve start time
    starttime = time.time()
    
    for i in range(c_count):
        p_data, p_ver = protectData(hostCRDP, c_data, protectionPolicy)

else:
    c_data_array = []
    p_data_array = []

    for i in range(c_count):
        c_data_array.append(c_data)

    # time - re-retrieve start time
    starttime = time.time()
    p_data_array, p_ver = protectBulkData(hostCRDP, c_data_array, protectionPolicy)

# time - get end time
endtime = time.time()
print("End time: ", endtime)

deltatimesec = (endtime-starttime)
pRate = c_count/deltatimesec

outStr = "\n* CRDP Test Completed. %s plaintext strings processed. Process time: %5.2f sec.  Rate: %5.2f tps. " %(c_count, deltatimesec, pRate) 
print(outStr)


    


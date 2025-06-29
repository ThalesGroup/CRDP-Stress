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
import string
from enum import Enum

#  --------- DEFINED Routines -----------------
def getRNDStr(t_len, t_choices):
    # Simple routine that generates a randon plaintext payload

    strRandom = "".join(
        random.choices(
            t_choices, k=t_len
        )
    )

    return strRandom
#

# ---------- CONSTANTS -------------------------
class charSet(Enum):
    alphanumeric    = "ALPHANUMERIC"
    digitsOnly      = "DIGITSONLY"
    printableascii  = "PRINTABLEASCII"

#####################################################################
# Code for collecting and parsing input information from command line
#####################################################################
parser = argparse.ArgumentParser()
parser.add_argument("-e", nargs=1, action="store", required=True, dest="hostnameCRDP")
parser.add_argument(
    "-p", nargs=1, action="store", required=True, dest="protectionPolicy"
)
parser.add_argument(
    "-b", nargs=1, action="store", required=False, dest="batchSize", type=int
)
parser.add_argument("-u", nargs=1, action="store", required=True, dest="username")
parser.add_argument("-bulk", action=argparse.BooleanOptionalAction)

parser.add_argument(
    "-f", nargs=1, action="store", required=False, dest="inFile", type=argparse.FileType('r'))

# Character Set Choice
parser.add_argument("-c", nargs=1, action="store", dest="charSetValue", required=False, 
                    choices=[charSet.alphanumeric.value,
                             charSet.digitsOnly.value,
                             charSet.printableascii.value],
                    default=[charSet.alphanumeric.value] )

args = parser.parse_args()

# Echo Input Parameters
hostCRDP = args.hostnameCRDP[0]

batchSize = 0
if args.batchSize:
    batchSize = args.batchSize[0]

protectionPolicy = args.protectionPolicy[0]
bulkFlag = False
if args.bulk:
    bulkFlag = True
r_user = args.username[0]

# Special processing if file is supplied.  Specifically, if it is provided
# we need to a) ensure the file exists and b) we process it as BULK
inFile = ""
fileSize = 0
if args.inFile:
    inFile = str(args.inFile[0].name)

    if os.path.isfile(inFile):
        bulkFlag = True
        fileSize = os.path.getsize(inFile)

if (batchSize == 0) and (inFile == ""):
    tmpStr = "\n*** CRDP ERROR:  Either Batchsize or Filename must be supplied.  Please supply either and try again. ***"
    print(colored(tmpStr, "yellow", attrs=["bold"]))
    exit()

# Collect the character set
charSetValue = str(" ".join(args.charSetValue))

tmpStr = "\nCDRP Stress starting... LET THE STRESS BEGIN!"
print(colored(tmpStr, "white", attrs=["underline"]))

#####################################################################
# Echo back input information for validation
#####################################################################
print(" Input Parameters:")

# include filename if it is specified

if len(inFile) > 0:
    
    tmpStr = (
        "  CRDPHost: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n  Input File: %s\n  File Size: %5.2f MB\n"
        % (hostCRDP, protectionPolicy, bulkFlag, inFile, fileSize/1000000)
    )
else:
    tmpStr = (
        "  CRDPHost: %s\n  BatchSize: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n  Character Set: %s\n"
        % (hostCRDP, batchSize, protectionPolicy, bulkFlag, charSetValue)
    )

print(tmpStr)

# Get a string of 64 random characters and treat is as cleartext (plaintext)

match(charSetValue):
    case charSet.alphanumeric.value:
        charValues = string.ascii_letters + string.digits
        p_data = getRNDStr(64, charValues)

    case charSet.digitsOnly.value:
        charValues = string.digits

        # Create a string of digits that follows the format of a credit card
        t_dataList = []
        t_dataList.append(getRNDStr(4, charValues))
        t_dataList.append(getRNDStr(4, charValues))
        t_dataList.append(getRNDStr(4, charValues))
        t_dataList.append(getRNDStr(4, charValues))
        
        p_data = '-'.join(t_dataList)

    case charSet.printableascii.value:
        charValues = string.printable
        p_data = getRNDStr(64, charValues)


# Reserve some variables for later use
p_data_array = []  # reserve for later use - cleartext (plaintext)
c_data = []  # reserve for later use - protectedtext
c_data_array = []  # reserve for later use - protectedtext
c_version = []  # reserve for later use - cipher version
r_data = []  # reserve for later use - revealedtext
r_data_array = []  # reserve for later use - revealtext

# how many times do we want to encrypt it?
if batchSize > 0:
    p_count = batchSize
    data_size = len(p_data)*p_count

if fileSize > 0:
    data_size = fileSize

#####################################################################
# Let's encrypt the data as fast as we can in two ways:
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP PROTECTION Test Started ***", "white", attrs=["bold"]))

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
            f_encode = base64.b64encode(f_content)
            f_encoded = str(f_encode)[1:] # strips leading 'b'
            data_size = fileSize

            # once you have encoded the characters, add them to the
            # plaintext array
            p_data_array.append(f_encoded)
    else:
        for i in range(p_count):
            p_data_array.append(p_data)

    print(" -->  CRDP Bulk PROTECT processing...")
    # time - re-retrieve start time
    starttime = time.time()
    c_data_array, c_version = protectBulkData(hostCRDP, p_data_array, protectionPolicy)
    
    # retreive first recorded in plaintext data
    p_data = p_data_array[0]
    
    # retreive first recorded in returned data
    c_data = c_data_array[0][
        CRDP_PROTECTED_DATA_NAME 
    ]


# time - get end time
endtime = time.time()
deltatimesec = endtime - starttime


if bulkFlag == True:
    pRate = (data_size / deltatimesec)/(1000000)  # MB/s

    outStr = (
        "\nCRDP Test Completed - PROTECT. %5.2f plaintext MBs processed. Process time: %5.2f sec.  Rate: %5.2f MB/s.\n"
        % (data_size/(1000000), deltatimesec, pRate)
    )
else:
    pRate = (data_size / deltatimesec)  # slower rate for non-bulk

    outStr = (
        "\nCRDP Test Completed - PROTECT. %s plaintext bytes processed. Process time: %5.2f sec.  Rate: %5.2f B/s.\n"
        % (data_size, deltatimesec, pRate)
    )


print(colored(outStr, "green", attrs=["bold"]))


#####################################################################
# Let's decrypt the data as fast as we can in two ways:
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP REVEAL Test Started ***", "white", attrs=["bold"]))

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

    # If a file was supplied, decode the returned data (base64) and change p_data to the actual file contents
    if len(inFile) > 0:
        tmpData = r_data_array[0][CRDP_DATA_NAME]
        r_data = base64.b64decode(tmpData)
        p_data = f_content


# time - get end time
endtime = time.time()
deltatimesec = endtime - starttime

if bulkFlag == True:
    pRate = (data_size / deltatimesec)/(1000000)  # MB/s

    outStr = (
        "\nCRDP Test Completed - REVEAL. %5.2f ciphertext MBs processed. Process time: %5.2f sec.  Rate: %5.2f MB/s.\n"
        % (data_size/(1000000), deltatimesec, pRate)
    )
else:
    pRate = (data_size / deltatimesec)  # slower rate for non-bulk

    outStr = (
        "\nCRDP Test Completed - REVEAL. %s ciphertext bytes processed. Process time: %5.2f sec.  Rate: %5.2f B/s.\n"
        % (data_size, deltatimesec, pRate)
    )

print(colored(outStr, "green", attrs=["bold"]))

outStr = "Plaintext (PT), CipherText (CT), and RevealText (RT) are as follows:"
print(outStr)

outStr = " PT: %s\n CT: %s\n RT: %s\n" % (p_data[0:63], c_data[0:63], r_data[0:63])
print(colored(outStr, "grey", attrs=["bold"]))
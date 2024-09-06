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


parser = argparse.ArgumentParser()
parser.add_argument('-i', nargs=1, action='store', required=True, dest='infile')
parser.add_argument('-o', nargs=1, action='store', required=True, dest='outfile')
parser.add_argument('-e', nargs=1, action='store', required=True, dest='hostnameCRDP')
parser.add_argument('-b', nargs=1, action='store', required=True, dest='batchSize', type=int)
parser.add_argument('-header', default=False, action=argparse.BooleanOptionalAction, dest='headerFlag')

args = parser.parse_args()

tmpStr = "\nCDRP Stress starting..."
print(tmpStr)
# time - get start time
starttime = time.time()

# Echo Input Parameters
inFile = args.infile[0]
outFile = args.outfile[0]
hostCRDP = args.hostnameCRDP[0]
batchSize = args.batchSize[0]
headerFlag = False
if args.headerFlag:
    headerFlag = True


print(" Input Parameters:")
tmpStr = "  InputFile: %s\n  Outputfile: %s\n  CRDPHost: %s\n  BatchSize: %s\n  HeaderPresent: %s\n" %(inFile, outFile, hostCRDP, batchSize, headerFlag)
print(tmpStr)

# Initialize dictionary for data from input CSV file
csvInputData = {} # dictionary

   
# Open CSV file and read
try:
    with open(inFile, 'r', newline='') as csvfile:
        csvReader = csv.reader(csvfile, dialect='excel')
        for row in csvReader:
            t_key, t_plainText = row
            csvInputData[t_key] = t_plainText

        dataSize = len(csvInputData)
        print("  ", inFile, "has been read.  It contains", dataSize, "rows.")
          
except:
    errStr = "ERROR Opening CSV File: %s" %inFile
    print(errStr)

exit()

# To have the computer perform some auto-checking of changes, take the hash file of the older
# output file (if it exists) for later comparison.

oldOutFileHash = 0
if path.exists(finalOutFile):
    with open(finalOutFile,"rb") as oOFN:
        tmpFN = oOFN.read()
        oldOutFileHash =  hashlib.md5(tmpFN).hexdigest()

# All files have been processed and all rows within each file have been processed. 
# csvMasterdata[] should now contain contents from all input files

rowCount = 0
with open(finalOutFile, 'w', newline='') as csvfile:
    csvWriter = csv.writer(csvfile, dialect='excel')

    for row in csvMasterData:
        csvWriter.writerow(row)  
        rowCount += 1
      
    print("  -> Final output file", finalOutFile, "has been written with", rowCount, "rows.")

newOutFileHash = 0
if path.exists(finalOutFile):
    with open(finalOutFile,"rb") as oOFN:
        tmpFN = oOFN.read()
        newOutFileHash =  hashlib.md5(tmpFN).hexdigest()

# For the sake of logging and notification, note if the hash value of the combined file is different from its 
# earlier version, if it existed.
fileChangeStr = "*CSV HASH CHANGE.* "
if newOutFileHash == oldOutFileHash:
    fileChangeStr = "No CSV hash change. "
    
prodCnt = rowCount - 1  # skip header row
endtime = time.time()
deltatimesec = (endtime-starttime)
pRate = prodCnt/deltatimesec

print("   old MD5 hash:", oldOutFileHash)
print("   new MD5 hash:", newOutFileHash)
outStr = "CSV File Merge App completed. %s files & %s products processed. Process time: %5.2f sec.  Rate: %5.2f pps. %s" %(fileCount, prodCnt, deltatimesec, pRate, fileChangeStr )
createProgressLogEntry(outStr)

if newOutFileHash != oldOutFileHash:
    postToSlack_GB_FFH(outStr)  # post to Slack only if there is a hash change
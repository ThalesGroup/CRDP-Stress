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
#           -p <protection policy name>
#           -u <username>
#           -batchsize <batch size>
#           -threads <parallel worker count>
#           -bulk - bulk submission flag
#           -payload <filename> - a single file encrypted in its entirety
#           -csvlist <filename> - a CSV file; every data cell is protected and a
#                                 <name>_protected<ext> copy is written at the end
#
import argparse
import time
from CRDP_REST_API import *
from parallel_execution import *
import random
from tqdm import tqdm
from termcolor import colored
import os
import csv
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
    "-batchsize", nargs=1, action="store", required=False, dest="batchSize", type=int
)
parser.add_argument("-u", nargs=1, action="store", required=True, dest="username")
parser.add_argument("-bulk", action=argparse.BooleanOptionalAction)
parser.add_argument(
    "-threads", nargs=1, action="store", required=False, dest="numTasks", type=int, default=[1]
)

fileGroup = parser.add_mutually_exclusive_group(required=False)
fileGroup.add_argument(
    "-payload", nargs=1, action="store", dest="payloadFile")
fileGroup.add_argument(
    "-csvlist", nargs=1, action="store", dest="csvListFile")

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

# Parse number of tasks (parallel workers)
numTasks = args.numTasks[0] if args.numTasks else 1
if numTasks < 1:
    tmpStr = "\n*** CRDP ERROR: Number of tasks must be >= 1. ***"
    print(colored(tmpStr, "yellow", attrs=["bold"]))
    exit()

payloadFile = ""
fileSize = 0
if args.payloadFile:
    payloadFile = str(args.payloadFile[0])

    if not os.path.isfile(payloadFile):
        tmpStr = "\n*** CRDP ERROR:  Payload file '%s' not found. ***" % payloadFile
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        exit()

    fileSize = os.path.getsize(payloadFile)

# CSV list mode - read the file now so the contents are available for the
# input echo and for building the workload.
csvListFile = ""
csvHeader = []
csvRows = []
csvCells = []
if args.csvListFile:
    csvListFile = str(args.csvListFile[0])

    if not os.path.isfile(csvListFile):
        tmpStr = "\n*** CRDP ERROR:  CSV list file '%s' not found. ***" % csvListFile
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        exit()

    with open(csvListFile, newline="") as cf:
        allRows = list(csv.reader(cf))

    if len(allRows) < 2:
        tmpStr = "\n*** CRDP ERROR:  CSV list file must contain a header row and at least one data row. ***"
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        exit()

    # First row is always preserved as a header; remaining rows are data.
    csvHeader = allRows[0]
    csvRows = allRows[1:]
    for row in csvRows:
        csvCells.extend(row)

if (batchSize == 0) and (payloadFile == "") and (csvListFile == ""):
    tmpStr = "\n*** CRDP ERROR:  Either Batchsize, -payload, or -csvlist must be supplied.  Please supply one and try again. ***"
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

if len(csvListFile) > 0:
    tmpStr = (
        "  CRDPHost: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n  CSV List File: %s\n  Data Rows: %s\n  Data Cells: %s\n  Parallel Tasks: %s\n"
        % (hostCRDP, protectionPolicy, bulkFlag, csvListFile, len(csvRows), len(csvCells), numTasks)
    )
elif len(payloadFile) > 0:
    batchLabel = "  Batch Size: %s\n" % batchSize if batchSize > 0 else ""
    tmpStr = (
        "  CRDPHost: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n  Payload File: %s\n  File Size: %5.2f MB\n%s  Parallel Tasks: %s\n"
        % (hostCRDP, protectionPolicy, bulkFlag, payloadFile, fileSize/1000000, batchLabel, numTasks)
    )
else:
    tmpStr = (
        "  CRDPHost: %s\n  BatchSize: %s\n  ProtectionPolicy: %s\n  BulkProtection: %s\n  Character Set: %s\n  Parallel Tasks: %s\n"
        % (hostCRDP, batchSize, protectionPolicy, bulkFlag, charSetValue, numTasks)
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
c_data_list = []  # reserve for later use - ordered discrete protectedtext
c_version = []  # reserve for later use - cipher version
r_data = []  # reserve for later use - revealedtext
r_data_array = []  # reserve for later use - revealtext

# In CSV list mode, every data cell is protected once (and the protected
# values are captured so the _protected file can be written at the end).
collectResults = len(csvListFile) > 0

# Build the workload (p_count items) and the plaintext array (p_data_array).
# p_data_array always has p_count entries so discrete and bulk paths share it.
f_content = None
badColumns = set()
if csvListFile:
    # Pre-screen each column: protect one sample (non-empty) cell per column.
    # Columns whose sample is rejected by the policy are skipped entirely; their
    # cells are left blank in the protected output.
    columnSample = {}
    for row in csvRows:
        for col, cell in enumerate(row):
            if col not in columnSample and cell != "":
                columnSample[col] = cell

    badColumnReason = {}
    for col, sample in columnSample.items():
        ok, msg = screenProtectPolicy(hostCRDP, sample, protectionPolicy)
        if not ok:
            badColumns.add(col)
            badColumnReason[col] = msg

    if badColumns:
        tmpStr = "  *** WARNING: Column(s) not processable under policy '%s':" % protectionPolicy
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        for c in sorted(badColumns):
            colName = csvHeader[c] if c < len(csvHeader) else "?"
            print(colored("      - %s (col %d) sample=%r" % (colName, c + 1, columnSample[c]), "yellow"))
            print(colored("        reason: %s" % badColumnReason[c], "yellow"))
        print(colored("      Cells in these columns will be left blank in the protected output.", "yellow"))

    # Build the workload from cells in good columns only. Empty cells are also
    # skipped since the policy cannot protect a zero-length value.
    p_data_array = [
        cell
        for row in csvRows
        for col, cell in enumerate(row)
        if col not in badColumns and cell != ""
    ]
    p_count = len(p_data_array)

    if p_count == 0:
        tmpStr = "\n*** CRDP ERROR:  No CSV cells can be processed under policy '%s'. ***" % protectionPolicy
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        exit()

    data_size = sum(len(cell.encode("utf-8")) for cell in p_data_array)
    numTasks = min(numTasks, p_count)
    p_data = p_data_array[0]

elif payloadFile:
    if batchSize > 0:
        p_count = batchSize
    elif numTasks > 1:
        p_count = numTasks
    else:
        p_count = 1
    data_size = fileSize * p_count
    numTasks = min(numTasks, p_count)

    with open(payloadFile, 'rb') as f:
        f_content = f.read()
        f_encoded = base64.b64encode(f_content).decode("ascii")

    p_data = f_encoded
    p_data_array = [f_encoded] * p_count

else:
    # Random plaintext mode - encrypt the same generated payload batchSize times.
    p_count = batchSize
    data_size = len(p_data) * p_count
    p_data_array = [p_data] * p_count

if numTasks > 1:
    base_per_thread = p_count // numTasks
    remainder = p_count % numTasks
    if remainder > 0:
        print(colored("  Items per thread: %d (%d threads get %d)" % (base_per_thread, remainder, base_per_thread + 1), "cyan"))
    else:
        print(colored("  Items per thread: %d" % base_per_thread, "cyan"))

#####################################################################
# Let's encrypt the data as fast as we can in two ways:
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP PROTECTION Test Started ***", "white", attrs=["bold"]))

# Check if parallel execution is requested
if numTasks > 1:
    # Parallel execution mode
    workload = distribute_workload(p_count, numTasks)

    # Execute parallel PROTECT
    agg_metrics, results, c_version = execute_protect_parallel(
        workload, bulkFlag, hostCRDP, p_data, p_data_array, protectionPolicy, collectResults
    )

    # Collect all results from all workers
    if results:
        if bulkFlag:
            # Combine encrypted results from all workers into one array
            c_data_array = []
            for task_id, metrics, worker_c_data_array, version in sorted(results, key=lambda x: x[0]):
                c_data_array.extend(worker_c_data_array)

            # Extract first item for validation display
            p_data = p_data_array[0]
            c_data = c_data_array[0][CRDP_PROTECTED_DATA_NAME]
        elif collectResults:
            # CSV list mode: combine every protected value, in order
            c_data_list = []
            for task_id, metrics, worker_c_data_list, version in sorted(results, key=lambda x: x[0]):
                c_data_list.extend(worker_c_data_list)
            c_data = c_data_list[0]
        else:
            # For discrete mode, just use first result
            _, _, c_data, _ = results[0]

    protect_agg_metrics = agg_metrics

else:
    # Sequential execution mode
    if bulkFlag == False:
        starttime = time.time()

        for i in tqdm(range(p_count), desc="Discrete PROTECT Progress"):
            c_data, c_version = protectData(hostCRDP, p_data_array[i], protectionPolicy)
            if collectResults:
                c_data_list.append(c_data)

        if collectResults:
            c_data = c_data_list[0]

    else:
        print(" -->  CRDP Bulk PROTECT processing...")
        starttime = time.time()
        c_data_array, c_version = protectBulkData(hostCRDP, p_data_array, protectionPolicy)

        p_data = p_data_array[0]
        c_data = c_data_array[0][CRDP_PROTECTED_DATA_NAME]

    # time - get end time and save for final summary
    endtime = time.time()
    protect_time = endtime - starttime


#####################################################################
# Let's decrypt the data as fast as we can in two ways:
# 1) as individual records or 2) as bulk data (faster)
#####################################################################
starttime = time.time()

print(colored("*** CRDP REVEAL Test Started ***", "white", attrs=["bold"]))

# Check if parallel execution is requested
if numTasks > 1:
    # Parallel execution mode
    # Distribute workload across tasks
    workload = distribute_workload(p_count, numTasks)

    # Execute parallel REVEAL
    agg_metrics, results = execute_reveal_parallel(
        workload, bulkFlag, hostCRDP, c_data, c_data_array, protectionPolicy, c_version, r_user
    )

    # Collect all results from all workers
    if results:
        if bulkFlag:
            # Combine revealed results from all workers into one array
            r_data_array = []
            for task_id, metrics, worker_r_data_array in sorted(results, key=lambda x: x[0]):
                r_data_array.extend(worker_r_data_array)

            # Extract first item for validation display
            r_data = r_data_array[0][CRDP_DATA_NAME]

            # If a file was supplied, decode the returned data (base64) and change p_data to the actual file contents
            if len(payloadFile) > 0:
                tmpData = r_data_array[0][CRDP_DATA_NAME]
                r_data = base64.b64decode(tmpData)
                p_data = f_content
        else:
            # For discrete mode, just use first result
            _, _, r_data = results[0]

            if len(payloadFile) > 0:
                r_data = base64.b64decode(r_data)
                p_data = f_content

    reveal_agg_metrics = agg_metrics

else:
    # Sequential execution mode
    if bulkFlag == False:
        starttime = time.time()

        for i in tqdm(range(p_count), desc="Discrete REVEAL Progress"):
            r_data = revealData(hostCRDP, c_data, protectionPolicy, c_version, r_user)

        if len(payloadFile) > 0:
            r_data = base64.b64decode(r_data)
            p_data = f_content

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
        if len(payloadFile) > 0:
            tmpData = r_data_array[0][CRDP_DATA_NAME]
            r_data = base64.b64decode(tmpData)
            p_data = f_content

    # time - get end time and save for final summary
    endtime = time.time()
    reveal_time = endtime - starttime


#####################################################################
# Final Summary - display CRDP Test Completed for both phases
#####################################################################
print(colored("\n==================== CRDP Test Summary ====================", "white", attrs=["bold"]))

if numTasks > 1:
    # Parallel mode: use aggregated metrics for summary with load distribution
    display_test_summary(protect_agg_metrics, data_size, "PROTECT", bulkFlag)
    display_test_summary(reveal_agg_metrics, data_size, "REVEAL", bulkFlag)
else:
    # Sequential mode: display completion messages from saved timing

    pRate_protect = (data_size / protect_time) / 1000000
    outStr = (
        "CRDP Test Completed - PROTECT. %5.3f MBs processed. Process time: %5.2f sec.  Rate: %5.3f MB/s."
        % (data_size / 1000000, protect_time, pRate_protect)
    )
    print(colored(outStr, "green", attrs=["bold"]))

    pRate_reveal = (data_size / reveal_time) / 1000000
    outStr = (
        "CRDP Test Completed - REVEAL. %5.3f MBs processed. Process time: %5.2f sec.  Rate: %5.3f MB/s."
        % (data_size / 1000000, reveal_time, pRate_reveal)
    )
    print(colored(outStr, "green", attrs=["bold"]))


print(colored("============================================================\n", "white", attrs=["bold"]))

outStr = "Plaintext (PT), CipherText (CT), and RevealText (RT) are as follows:"
print(outStr)

outStr = " PT: %s\n CT: %s\n RT: %s\n" % (p_data[0:63], c_data[0:63], r_data[0:63])
print(colored(outStr, "grey", attrs=["bold"]))

#####################################################################
# CSV list mode - write the _protected copy once, after the round trip.
# The header row is preserved as-is; every data cell is replaced with its
# protected/tokenized equivalent.
#####################################################################
if csvListFile:
    if bulkFlag:
        protected_values = [item[CRDP_PROTECTED_DATA_NAME] for item in c_data_array]
    else:
        protected_values = c_data_list

    base, ext = os.path.splitext(csvListFile)
    protectedFile = base + "_protected" + ext

    # protected_values is in the same row-major order as the workload: one entry
    # per good, non-empty cell. Skipped cells (bad columns or empty) are blank.
    with open(protectedFile, "w", newline="") as pf:
        writer = csv.writer(pf)
        writer.writerow(csvHeader)
        pv_idx = 0
        for row in csvRows:
            out_row = []
            for col, cell in enumerate(row):
                if col not in badColumns and cell != "":
                    out_row.append(protected_values[pv_idx])
                    pv_idx += 1
                else:
                    out_row.append("")
            writer.writerow(out_row)

    outStr = "Protected CSV written to: %s  (%d data rows)" % (protectedFile, len(csvRows))
    print(colored(outStr, "green", attrs=["bold"]))

#####################################################################
# Payload mode - write the protected payload once, after the round trip.
# An existing "_protected" file is overwritten.
#####################################################################
if payloadFile:
    base, ext = os.path.splitext(payloadFile)
    protectedFile = base + "_protected" + ext

    with open(protectedFile, "w") as pf:
        pf.write(c_data)

    outStr = "Protected payload written to: %s" % protectedFile
    print(colored(outStr, "green", attrs=["bold"]))
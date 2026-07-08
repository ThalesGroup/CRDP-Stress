# CRDP Stress
#
# Opens a CSV file and then submits data in second, to N fields to a CRDP
# instance.  Since CRDP handles batch encryption, the data read from the CSV
# file will be organized into batches before being submitted to the CRDP instance.
#
# Time will be measured for the file to be encrypted excluding file I/O actions.
#
# Usage:  CRDP_Stress.py
#           -endpoint <CRDP endpoint hostname or IP address>
#           -policy <protection policy name>
#           -user <username>
#           -iterations <iteration count>
#           -batchsize <plaintext payloads per bulk message; 0 = all-in-one>
#           -threads <parallel worker count>
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
import json
import socket
import base64
import string
from datetime import datetime
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
parser.add_argument("-endpoint", nargs=1, action="store", required=True, dest="endpointCRDP", help="CRDP Endpoint FQDN or IP Address")
parser.add_argument(
    "-policy", nargs=1, action="store", required=True, dest="protectionPolicy", help="CRDP Protection Policy Name"
)
parser.add_argument(
    "-iterations", nargs=1, action="store", required=False, dest="iterations", type=int, help="Number of times plaintext will be processed by CRDP"
)
parser.add_argument("-user", nargs=1, action="store", required=True, dest="username", help="Username of user to be used against Access Policy")
parser.add_argument(
    "-batchsize", nargs=1, action="store", required=False, dest="batchsize", type=int, default=[1],
    help="Number of plaintext payloads sent in a single message to CRDP.  Use a value of 0 if all plaintext iterations or plaintext messages should be sent in a single message."
)
parser.add_argument(
    "-threads", nargs=1, action="store", required=False, dest="numThreads", type=int, default=[1], metavar="NUMTHREADS", help="Number of concurrent client threads sending data to CRDP for processing"
)
parser.add_argument(
    "-jsonout", nargs=1, action="store", required=False, dest="jsonout",
    help="Write machine-readable results (txns/sec, latency percentiles, rolling throughput, client CPU) to this JSON file for run-to-run comparison"
)
parser.add_argument(
    "-label", nargs=1, action="store", required=False, dest="label",
    help="Optional tag recorded in the JSON results to identify this run (e.g. testA-4clients)"
)

fileGroup = parser.add_mutually_exclusive_group(required=False)
fileGroup.add_argument(
    "-payload", nargs=1, action="store", dest="payloadFile", help="Binary or image file that will be used as plaintext")
fileGroup.add_argument(
    "-csvlist", nargs=1, action="store", dest="csvListFile", help="csv file with columns of plaintext data for encryption or tokenization")

# Character Set Choice
parser.add_argument("-charset", nargs=1, action="store", dest="charSetValue", required=False,
                    choices=[charSet.alphanumeric.value,
                             charSet.digitsOnly.value,
                             charSet.printableascii.value],
                    default=[charSet.digitsOnly.value],
                    help="Character set used when random plaintext needs to be generated (ignored when -payload or -csvlist is used)")

args = parser.parse_args()

# Echo Input Parameters
endpointCRDP = args.endpointCRDP[0]

iterations = 0
if args.iterations:
    iterations = args.iterations[0]

batchsize = args.batchsize[0]
if batchsize < 0:
    tmpStr = "\n*** CRDP ERROR:  -batchsize must be 0 or a positive integer. ***"
    print(colored(tmpStr, "yellow", attrs=["bold"]))
    exit()

protectionPolicy = args.protectionPolicy[0]
r_user = args.username[0]

jsonout = args.jsonout[0] if args.jsonout else ""
runLabel = args.label[0] if args.label else ""

# Parse number of tasks (parallel workers)
numThreads = args.numThreads[0] if args.numThreads else 1
if numThreads < 1:
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

# When no source is specified (no -iterations, no -payload, no -csvlist),
# default to a single random-plaintext iteration.
if iterations == 0:
    iterations = 1

# Collect the character set
charSetValue = str(" ".join(args.charSetValue))

tmpStr = "\nCDRP Stress starting... LET THE STRESS BEGIN!"
print(colored(tmpStr, "white", attrs=["underline"]))

#####################################################################
# Echo back input information for validation
#####################################################################
print(" Input Parameters:")

# include filename if it is specified

batchsizeLabel = "all-in-one (0)" if batchsize == 0 else str(batchsize)

if len(csvListFile) > 0:
    tmpStr = (
        "  CRDPEndpoint: %s\n  ProtectionPolicy: %s\n  CSV List File: %s\n  Data Rows: %s\n  Data Cells: %s\n  Iterations: %s\n  Batch Size: %s\n  Parallel Tasks: %s\n"
        % (endpointCRDP, protectionPolicy, csvListFile, len(csvRows), len(csvCells), iterations, batchsizeLabel, numThreads)
    )
elif len(payloadFile) > 0:
    tmpStr = (
        "  CRDPEndpoint: %s\n  ProtectionPolicy: %s\n  Payload File: %s\n  File Size: %5.2f MB\n  Iterations: %s\n  Batch Size: %s\n  Parallel Tasks: %s\n"
        % (endpointCRDP, protectionPolicy, payloadFile, fileSize/1000000, iterations, batchsizeLabel, numThreads)
    )
else:
    tmpStr = (
        "  CRDPEndpoint: %s\n  Iterations: %s\n  Batch Size: %s\n  ProtectionPolicy: %s\n  Character Set: %s\n  Parallel Tasks: %s\n"
        % (endpointCRDP, iterations, batchsizeLabel, protectionPolicy, charSetValue, numThreads)
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

    case _:
        # argparse `choices` already constrains -charset, so this is unreachable
        # today. It exists so p_data is provably bound below, and so that adding
        # a charSet member without a branch here fails loudly instead of silently
        # producing no plaintext.
        tmpStr = "\n*** CRDP ERROR:  Unsupported character set '%s'. ***" % charSetValue
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        exit()


# Reserve some variables for later use
p_data_array = []  # reserve for later use - cleartext (plaintext)
c_data = []  # reserve for later use - protectedtext
c_data_array = []  # reserve for later use - protectedtext
c_version = []  # reserve for later use - cipher version
r_data = []  # reserve for later use - revealedtext
r_data_array = []  # reserve for later use - revealtext

# Build the workload (p_count items) and the plaintext array (p_data_array).
# Then split p_data_array into bulk messages of size `batchsize`.
f_content = None
badColumns = set()
base_cell_count = 0  # CSV mode: cells per iteration (for _protected.csv output)
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
        ok, msg = screenProtectPolicy(endpointCRDP, sample, protectionPolicy)
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
    base_cells = [
        cell
        for row in csvRows
        for col, cell in enumerate(row)
        if col not in badColumns and cell != ""
    ]
    base_cell_count = len(base_cells)

    if base_cell_count == 0:
        tmpStr = "\n*** CRDP ERROR:  No CSV cells can be processed under policy '%s'. ***" % protectionPolicy
        print(colored(tmpStr, "yellow", attrs=["bold"]))
        exit()

    # Repeat the cell list `iterations` times for stress repetition. The
    # _protected.csv output is written from the first iteration's results only.
    p_data_array = base_cells * iterations
    p_count = len(p_data_array)
    data_size = sum(len(cell.encode("utf-8")) for cell in p_data_array)
    p_data = p_data_array[0]

elif payloadFile:
    p_count = iterations
    data_size = fileSize * p_count

    with open(payloadFile, 'rb') as f:
        f_content = f.read()
        f_encoded = base64.b64encode(f_content).decode("ascii")

    p_data = f_encoded
    p_data_array = [f_encoded] * p_count

else:
    # Random plaintext mode - encrypt the same generated payload `iterations` times.
    p_count = iterations
    data_size = len(p_data) * p_count
    p_data_array = [p_data] * p_count

# Split the flat plaintext array into bulk messages. batchsize == 0 means
# everything goes in one message; otherwise chunks of `batchsize` (last may be smaller).
if batchsize == 0:
    messages = [p_data_array]
else:
    messages = [p_data_array[i:i + batchsize] for i in range(0, p_count, batchsize)]
message_count = len(messages)

# Cap thread count to the number of messages - no benefit in having idle workers.
numThreads = min(numThreads, message_count)

print(colored("  Total payloads: %d  |  Messages: %d  |  Workers: %d" % (p_count, message_count, numThreads), "cyan"))

#####################################################################
# PROTECT phase: every call goes through the bulk REST API. The plaintext
# array has been split into `messages` (each a list of `batchsize` payloads).
# With numThreads > 1, messages are distributed round-robin across workers.
#####################################################################
print(colored("*** CRDP PROTECTION Test Started ***", "white", attrs=["bold"]))

protect_cpu = ClientCpuSampler().start()
if numThreads > 1:
    starttime = time.time()
    protect_agg_metrics, c_data_array, c_version = execute_protect_messages_parallel(
        messages, numThreads, endpointCRDP, protectionPolicy
    )
    endtime = time.time()
    protect_time = endtime - starttime
else:
    starttime = time.time()
    c_data_array = []
    c_version = None
    protect_records = []
    for msg in tqdm(messages, desc="Bulk PROTECT Progress"):
        call_start = time.time()
        chunk, version = protectBulkData(endpointCRDP, msg, protectionPolicy)
        call_end = time.time()
        protect_records.append((call_start, call_end, len(msg)))
        c_data_array.extend(chunk)
        if c_version is None:
            c_version = version
    endtime = time.time()
    protect_time = endtime - starttime
    # Build the same rich metrics object the parallel path produces so the
    # single-thread baseline is directly comparable.
    protect_agg_metrics = single_worker_aggregate(protect_records, starttime, endtime)
protect_cpu.stop()

p_data = p_data_array[0]
c_data = c_data_array[0][CRDP_PROTECTED_DATA_NAME]


#####################################################################
# REVEAL phase: re-chunk the protected data into messages of `batchsize`
# and submit through the bulk REVEAL API using the same scheme.
#####################################################################
print(colored("*** CRDP REVEAL Test Started ***", "white", attrs=["bold"]))

if batchsize == 0:
    reveal_messages = [c_data_array]
else:
    reveal_messages = [c_data_array[i:i + batchsize] for i in range(0, len(c_data_array), batchsize)]

reveal_cpu = ClientCpuSampler().start()
if numThreads > 1:
    starttime = time.time()
    reveal_agg_metrics, r_data_array = execute_reveal_messages_parallel(
        reveal_messages, numThreads, endpointCRDP, protectionPolicy, c_version, r_user
    )
    endtime = time.time()
    reveal_time = endtime - starttime
else:
    starttime = time.time()
    r_data_array = []
    reveal_records = []
    for msg in tqdm(reveal_messages, desc="Bulk REVEAL Progress"):
        call_start = time.time()
        chunk = revealBulkData(endpointCRDP, msg, protectionPolicy, c_version, r_user)
        call_end = time.time()
        reveal_records.append((call_start, call_end, len(msg)))
        r_data_array.extend(chunk)
    endtime = time.time()
    reveal_time = endtime - starttime
    reveal_agg_metrics = single_worker_aggregate(reveal_records, starttime, endtime)
reveal_cpu.stop()

r_data = r_data_array[0][CRDP_DATA_NAME]
if len(payloadFile) > 0:
    r_data = base64.b64decode(r_data)
    if f_content is not None:
        # Display the original file bytes rather than the base64 we transmitted.
        # f_content is always populated in payload mode; the guard keeps p_data
        # non-Optional for the summary print below.
        p_data = f_content


#####################################################################
# Final Summary - display CRDP Test Completed for both phases
#####################################################################
print(colored("\n==================== CRDP Test Summary ====================", "white", attrs=["bold"]))

# Both paths now produce an AggregatedMetrics, so the summary (MB/s plus the
# txns/sec, latency, rolling-throughput, and client-CPU attribution lines) is
# rendered the same way whether the run was sequential or parallel.
display_test_summary(protect_agg_metrics, data_size, "PROTECT", protect_cpu)
display_test_summary(reveal_agg_metrics, data_size, "REVEAL", reveal_cpu)


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
    # Only the first iteration's protected values feed the output file -
    # subsequent iterations are duplicate stress passes over the same cells.
    protected_values = [item[CRDP_PROTECTED_DATA_NAME] for item in c_data_array[:base_cell_count]]

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

#####################################################################
# Machine-readable results (-jsonout) - one comparable record per run,
# capturing the attribution metrics for the client / ingress / backend
# experiment matrix.
#####################################################################
if jsonout:
    if csvListFile:
        mode = "csvlist"
        source = csvListFile
    elif payloadFile:
        mode = "payload"
        source = payloadFile
    else:
        mode = "random"
        source = charSetValue

    result = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "hostname": socket.gethostname(),
        "label": runLabel,
        "params": {
            "endpoint": endpointCRDP,
            "protection_policy": protectionPolicy,
            "mode": mode,
            "source": source,
            "iterations": iterations,
            "batchsize": batchsize,
            "threads": numThreads,
            "total_payloads": p_count,
            "message_count": message_count,
            "data_size_bytes": data_size,
        },
        "protect": build_phase_record(protect_agg_metrics, data_size, protect_cpu, "PROTECT"),
        "reveal": build_phase_record(reveal_agg_metrics, data_size, reveal_cpu, "REVEAL"),
    }

    with open(jsonout, "w") as jf:
        json.dump(result, jf, indent=2)

    print(colored("Results written to: %s" % jsonout, "green", attrs=["bold"]))
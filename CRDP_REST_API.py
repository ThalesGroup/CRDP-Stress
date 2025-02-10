# CRDP REST API Commands
#
# Definition file of assorted REST Commands for communicating
# with CRDP.
#
# Reference: https://thalesdocs.com/ctp/con/crdp/latest/
# API Ref: https://thalesdocs.com/ctp/con/crdp/latest/crdp-apis/index.html
#
######################################################################
import requests
import json


# ---------------- CONSTANTS -----------------------------------------------------
STATUS_CODE_OK = 200
NET_TIMEOUT = 600

CRDP_PROTECT = "/v1/protect"
CRDP_REVEAL = "/v1/reveal"
CRDP_BULK_PROTECT = "/v1/protectbulk"
CRDP_BULK_REVEAL = "/v1/revealbulk"
CRDP_PROTECTION_POLICY_NAME = "protection_policy_name"
CRDP_DATA_NAME = "data"
CRDP_DATA_ARRAY_NAME = "data_array"
CRDP_PROTECTED_DATA_NAME = "protected_data"
CRDP_PROTECTED_DATA_ARRAY_NAME = "protected_data_array"
CRDP_EXTERNAL_VER_NAME = "external_version"
CRDP_USERNAME_NAME = "username"

APP_CONTENT_TYPE = "Content-Type"
APP_JSON = "application/json"


def protectData(t_hostCRDP, t_data, t_protectionPolicy):
    # -----------------------------------------------------------------------------
    # REST Assembly for data protection
    #
    # Assemble and send the command to CRDP for protecting (encrypting) data and
    # retrieve the result and the external version.
    # -----------------------------------------------------------------------------
    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_PROTECT)

    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_DATA_NAME: t_data,
    }

    # Now that everything is populated, assemble and post command
    try:
        r = requests.post(
            t_endpoint, data=json.dumps(t_dataStr), headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("protectData-exception:\n", e)
        exit()

    if r.status_code != STATUS_CODE_OK:
        kPrintError("protectData", r)
        exit()

    # Extract the UserAuthId from the value of the key-value pair of the JSON reponse.
    t_protectedData = r.json()[CRDP_PROTECTED_DATA_NAME]
    t_version = r.json()[CRDP_EXTERNAL_VER_NAME]

    return t_protectedData, t_version


def protectBulkData(t_hostCRDP, t_dataArray, t_protectionPolicy):
    # -----------------------------------------------------------------------------
    # REST Assembly for bulk data protection
    #
    # Assemble and send the command to CRDP for protecting (encrypting) data and
    # retrieve the result and the external version as an array.
    # -----------------------------------------------------------------------------
    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_BULK_PROTECT)

    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_DATA_ARRAY_NAME: t_dataArray,
    }

    # Now that everything is populated, assemble and post command
    try:
        r = requests.post(
            t_endpoint, data=json.dumps(t_dataStr), headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("protectBulkData-exception:\n", e)
        exit()

    if r.status_code != STATUS_CODE_OK:
        kPrintError("protectBulkData", r)
        exit()

    # Extract the UserAuthId from the value of the key-value pair of the JSON reponse.
    t_protectedData = r.json()[CRDP_PROTECTED_DATA_ARRAY_NAME]
    t_version = r.json()[CRDP_PROTECTED_DATA_ARRAY_NAME][0][CRDP_EXTERNAL_VER_NAME]

    return t_protectedData, t_version


def revealData(t_hostCRDP, t_data, t_protectionPolicy, t_externalVersion, t_user):
    # -----------------------------------------------------------------------------
    # REST Assembly for data reveal
    #
    # Assemble and send the command to CRDP for reveal (decrypting) data and
    # retrieve the result and the external version.
    # -----------------------------------------------------------------------------
    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_REVEAL)

    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_EXTERNAL_VER_NAME: t_externalVersion,
        CRDP_USERNAME_NAME: t_user,
        CRDP_PROTECTED_DATA_NAME: t_data,
    }

    # Now that everything is populated, assemble and post command
    try:
        r = requests.post(
            t_endpoint, data=json.dumps(t_dataStr), headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("revealData-exception:\n", e)
        exit()

    if r.status_code != STATUS_CODE_OK:
        kPrintError("revealData", r)
        exit()

    # Extract the UserAuthId from the value of the key-value pair of the JSON reponse.
    t_revealedData = r.json()[CRDP_DATA_NAME]

    return t_revealedData


def revealBulkData(
    t_hostCRDP, t_dataArray, t_protectionPolicy, t_externalVersion, t_user
):
    # -----------------------------------------------------------------------------
    # REST Assembly for bulk data reveal
    #
    # Assemble and send the command to CRDP for prevealingg (decrypting) bulk data and
    # retrieve the result as an array.
    # -----------------------------------------------------------------------------
    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_BULK_REVEAL)

    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_USERNAME_NAME: t_user,
        CRDP_PROTECTED_DATA_ARRAY_NAME: t_dataArray,
    }

    # Now that everything is populated, assemble and post command
    try:
        r = requests.post(
            t_endpoint, data=json.dumps(t_dataStr), headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("revealBulkData-exception:\n", e)
        exit()

    if r.status_code != STATUS_CODE_OK:
        kPrintError("revealBulkData", r)
        exit()

    # Extract the UserAuthId from the value of the key-value pair of the JSON reponse.
    t_revealedDataArray = r.json()[CRDP_DATA_ARRAY_NAME]

    return t_revealedDataArray


def kPrintError(t_str, t_r):
    # -----------------------------------------------------------------------------
    # The objective is to print the error information back in the even that a HTTPS
    # response is not STATUS_OK
    # -----------------------------------------------------------------------------
    t_str_sc = str(t_r.status_code)
    t_str_r = str(t_r.reason)
    # t_str_e     = str(t_r.json()['error'])
    # t_str_e = str(t_r.json())
    t_str_e = t_r

    tmpstr = "  --> %s Status Code: %s\n   Reason: %s\n   Error: %s" % (
        t_str,
        t_str_sc,
        t_str_r,
        t_str_e,
    )

    print(tmpstr)

    return


def makeHexStr(t_val):
    # -------------------------------------------------------------------------------
    # makeHexString
    # -------------------------------------------------------------------------------
    tmpStr = str(t_val)
    t_hexStr = hex(int("0x" + tmpStr[2:-1], 0))

    return t_hexStr


def printJList(t_str, t_jList):
    # -------------------------------------------------------------------------------
    # A quick subscript that makes it easy to print out a list of JSON information in
    # a more readable format.
    # -------------------------------------------------------------------------------
    print("\n ", t_str, json.dumps(t_jList, skipkeys=True, allow_nan=True, indent=3))

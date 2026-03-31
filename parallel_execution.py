# Parallel Execution Module for CRDP Stress Testing
#
# This module provides parallel execution capabilities for stress testing
# CRDP servers using ThreadPoolExecutor. It includes worker functions,
# metrics collection, and workload distribution logic.
#
######################################################################
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm
from termcolor import colored
from CRDP_REST_API import (
    protectData, protectBulkData, revealData, revealBulkData,
    CRDP_PROTECTED_DATA_NAME, CRDP_DATA_NAME, CRDP_EXTERNAL_VER_NAME
)


# -------------------- Metrics Classes --------------------

class WorkerMetrics:
    """Metrics collected by each worker thread"""
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.start_time = None
        self.end_time = None
        self.items_processed = 0
        self.errors = []

    def duration(self):
        """Return duration in seconds"""
        return self.end_time - self.start_time if self.end_time else 0


class AggregatedMetrics:
    """Aggregated metrics across all workers"""
    def __init__(self):
        self.overall_start = None
        self.overall_end = None
        self.worker_metrics = []
        self.total_items = 0

    def add_worker_metrics(self, metrics):
        """Add metrics from a single worker"""
        self.worker_metrics.append(metrics)
        self.total_items += metrics.items_processed

    def overall_duration(self):
        """Total wall-clock time"""
        return self.overall_end - self.overall_start if self.overall_end else 0

    def worker_durations(self):
        """List of all worker durations"""
        return [m.duration() for m in self.worker_metrics]

    def avg_worker_duration(self):
        """Average worker duration"""
        durations = self.worker_durations()
        return sum(durations) / len(durations) if durations else 0

    def min_worker_duration(self):
        """Minimum worker duration"""
        durations = self.worker_durations()
        return min(durations) if durations else 0

    def max_worker_duration(self):
        """Maximum worker duration"""
        durations = self.worker_durations()
        return max(durations) if durations else 0

    def load_skew_percent(self):
        """Calculate load imbalance as percentage"""
        min_dur = self.min_worker_duration()
        max_dur = self.max_worker_duration()
        if max_dur == 0:
            return 0
        return ((max_dur - min_dur) / max_dur) * 100


# -------------------- Workload Distribution --------------------

def distribute_workload(total_count, num_tasks):
    """
    Divide total_count into num_tasks chunks.
    Returns list of (start_index, count) tuples.

    Example: distribute_workload(1000, 3) -> [(0, 334), (334, 333), (667, 333)]
    """
    if num_tasks <= 0:
        raise ValueError("num_tasks must be > 0")
    if total_count <= 0:
        raise ValueError("total_count must be > 0")

    base_size = total_count // num_tasks
    remainder = total_count % num_tasks

    workload = []
    current_start = 0

    for i in range(num_tasks):
        # Distribute remainder across first few tasks
        chunk_size = base_size + (1 if i < remainder else 0)
        workload.append((current_start, chunk_size))
        current_start += chunk_size

    return workload


# -------------------- Session-based API Wrappers --------------------

def protectData_session(session, t_hostCRDP, t_data, t_protectionPolicy):
    """
    Session-aware version of protectData.
    Uses provided session instead of creating new connection.
    """
    from CRDP_REST_API import (
        CRDP_PROTECT, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_DATA_NAME,
        CRDP_PROTECTED_DATA_NAME, CRDP_EXTERNAL_VER_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_PROTECT)
    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_DATA_NAME: t_data,
    }

    try:
        r = session.post(
            t_endpoint, data=__import__('json').dumps(t_dataStr),
            headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("protectData_session-exception:\n", e)
        raise

    if r.status_code != STATUS_CODE_OK:
        kPrintError("protectData_session", r)
        raise Exception(f"HTTP {r.status_code}")

    t_protectedData = r.json()[CRDP_PROTECTED_DATA_NAME]
    t_version = r.json()[CRDP_EXTERNAL_VER_NAME]

    return t_protectedData, t_version


def protectBulkData_session(session, t_hostCRDP, t_dataArray, t_protectionPolicy):
    """
    Session-aware version of protectBulkData.
    """
    from CRDP_REST_API import (
        CRDP_BULK_PROTECT, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_DATA_ARRAY_NAME,
        CRDP_PROTECTED_DATA_ARRAY_NAME, CRDP_EXTERNAL_VER_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_BULK_PROTECT)
    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_DATA_ARRAY_NAME: t_dataArray,
    }

    try:
        r = session.post(
            t_endpoint, data=__import__('json').dumps(t_dataStr),
            headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("protectBulkData_session-exception:\n", e)
        raise

    if r.status_code != STATUS_CODE_OK:
        kPrintError("protectBulkData_session", r)
        raise Exception(f"HTTP {r.status_code}")

    t_protectedData = r.json()[CRDP_PROTECTED_DATA_ARRAY_NAME]
    t_version = r.json()[CRDP_PROTECTED_DATA_ARRAY_NAME][0][CRDP_EXTERNAL_VER_NAME]

    return t_protectedData, t_version


def revealData_session(session, t_hostCRDP, t_data, t_protectionPolicy, t_externalVersion, t_user):
    """
    Session-aware version of revealData.
    """
    from CRDP_REST_API import (
        CRDP_REVEAL, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_EXTERNAL_VER_NAME,
        CRDP_USERNAME_NAME, CRDP_PROTECTED_DATA_NAME, CRDP_DATA_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_REVEAL)
    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_EXTERNAL_VER_NAME: t_externalVersion,
        CRDP_USERNAME_NAME: t_user,
        CRDP_PROTECTED_DATA_NAME: t_data,
    }

    try:
        r = session.post(
            t_endpoint, data=__import__('json').dumps(t_dataStr),
            headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("revealData_session-exception:\n", e)
        raise

    if r.status_code != STATUS_CODE_OK:
        kPrintError("revealData_session", r)
        raise Exception(f"HTTP {r.status_code}")

    t_revealedData = r.json()[CRDP_DATA_NAME]

    return t_revealedData


def revealBulkData_session(session, t_hostCRDP, t_dataArray, t_protectionPolicy, t_externalVersion, t_user):
    """
    Session-aware version of revealBulkData.
    """
    from CRDP_REST_API import (
        CRDP_BULK_REVEAL, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_USERNAME_NAME,
        CRDP_PROTECTED_DATA_ARRAY_NAME, CRDP_DATA_ARRAY_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_hostCRDP, CRDP_BULK_REVEAL)
    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_USERNAME_NAME: t_user,
        CRDP_PROTECTED_DATA_ARRAY_NAME: t_dataArray,
    }

    try:
        r = session.post(
            t_endpoint, data=__import__('json').dumps(t_dataStr),
            headers=t_headers, verify=False, timeout=NET_TIMEOUT
        )
    except requests.exceptions.RequestException as e:
        print("revealBulkData_session-exception:\n", e)
        raise

    if r.status_code != STATUS_CODE_OK:
        kPrintError("revealBulkData_session", r)
        raise Exception(f"HTTP {r.status_code}")

    t_revealedDataArray = r.json()[CRDP_DATA_ARRAY_NAME]

    return t_revealedDataArray


# -------------------- Worker Functions --------------------

def worker_protect_discrete(task_id, start_idx, count, hostCRDP, p_data, protectionPolicy, pbar, lock):
    """
    Worker function for discrete PROTECT operations.
    Each worker makes individual protectData calls for its assigned chunk.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    c_data = None
    c_version = None

    try:
        for i in range(count):
            c_data, c_version = protectData_session(session, hostCRDP, p_data, protectionPolicy)

            # Thread-safe progress update
            with lock:
                pbar.update(1)

        metrics.items_processed = count
    except Exception as e:
        metrics.errors.append(str(e))
        print(colored(f"\nWorker {task_id} error: {e}", "red"))
    finally:
        metrics.end_time = time.time()
        session.close()

    return metrics, c_data, c_version


def worker_protect_bulk(task_id, data_chunk, hostCRDP, protectionPolicy, pbar, lock):
    """
    Worker function for bulk PROTECT operations.
    Each worker makes ONE protectBulkData call with its data chunk.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    c_data_array = None
    c_version = None

    try:
        c_data_array, c_version = protectBulkData_session(session, hostCRDP, data_chunk, protectionPolicy)
        metrics.items_processed = len(data_chunk)

        # Update progress bar once (bulk completes in one shot)
        with lock:
            pbar.update(len(data_chunk))
    except Exception as e:
        metrics.errors.append(str(e))
        print(colored(f"\nWorker {task_id} error: {e}", "red"))
    finally:
        metrics.end_time = time.time()
        session.close()

    return metrics, c_data_array, c_version


def worker_reveal_discrete(task_id, start_idx, count, hostCRDP, c_data, protectionPolicy, c_version, r_user, pbar, lock):
    """
    Worker function for discrete REVEAL operations.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    r_data = None

    try:
        for i in range(count):
            r_data = revealData_session(session, hostCRDP, c_data, protectionPolicy, c_version, r_user)

            # Thread-safe progress update
            with lock:
                pbar.update(1)

        metrics.items_processed = count
    except Exception as e:
        metrics.errors.append(str(e))
        print(colored(f"\nWorker {task_id} error: {e}", "red"))
    finally:
        metrics.end_time = time.time()
        session.close()

    return metrics, r_data


def worker_reveal_bulk(task_id, data_chunk, hostCRDP, protectionPolicy, c_version, r_user, pbar, lock):
    """
    Worker function for bulk REVEAL operations.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    r_data_array = None

    try:
        r_data_array = revealBulkData_session(session, hostCRDP, data_chunk, protectionPolicy, c_version, r_user)
        metrics.items_processed = len(data_chunk)

        # Update progress bar once
        with lock:
            pbar.update(len(data_chunk))
    except Exception as e:
        metrics.errors.append(str(e))
        print(colored(f"\nWorker {task_id} error: {e}", "red"))
    finally:
        metrics.end_time = time.time()
        session.close()

    return metrics, r_data_array


# -------------------- Orchestration Functions --------------------

def execute_protect_parallel(workload, bulkFlag, hostCRDP, p_data, p_data_array, protectionPolicy):
    """
    Execute parallel PROTECT operations using ThreadPoolExecutor.

    Args:
        workload: List of (start_idx, count) tuples from distribute_workload()
        bulkFlag: Boolean indicating bulk mode
        hostCRDP: CRDP server hostname
        p_data: Single plaintext data (for discrete mode)
        p_data_array: Array of plaintext data (for bulk mode)
        protectionPolicy: Protection policy name

    Returns:
        AggregatedMetrics, list of results, c_version
    """
    num_tasks = len(workload)
    total_items = sum(count for _, count in workload)

    # Create shared progress bar and lock
    progress_lock = Lock()
    desc = "Parallel PROTECT Progress"

    # Aggregated metrics
    agg_metrics = AggregatedMetrics()
    agg_metrics.overall_start = time.time()

    results = []
    c_version = None

    with tqdm(total=total_items, desc=desc) as pbar:
        with ThreadPoolExecutor(max_workers=num_tasks) as executor:
            futures = {}

            # Submit workers based on mode
            if bulkFlag:
                # Bulk mode: divide p_data_array into chunks
                for task_id, (start_idx, count) in enumerate(workload):
                    data_chunk = p_data_array[start_idx:start_idx + count]
                    future = executor.submit(
                        worker_protect_bulk,
                        task_id, data_chunk, hostCRDP, protectionPolicy, pbar, progress_lock
                    )
                    futures[future] = task_id
            else:
                # Discrete mode: each worker makes individual calls
                for task_id, (start_idx, count) in enumerate(workload):
                    future = executor.submit(
                        worker_protect_discrete,
                        task_id, start_idx, count, hostCRDP, p_data, protectionPolicy, pbar, progress_lock
                    )
                    futures[future] = task_id

            # Collect results as workers complete
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    metrics, data, version = future.result()
                    results.append((task_id, metrics, data, version))
                    agg_metrics.add_worker_metrics(metrics)
                    if c_version is None:
                        c_version = version
                except Exception as e:
                    print(colored(f"\nWorker {task_id} failed: {e}", "red"))

    agg_metrics.overall_end = time.time()

    return agg_metrics, results, c_version


def execute_reveal_parallel(workload, bulkFlag, hostCRDP, c_data, c_data_array, protectionPolicy, c_version, r_user):
    """
    Execute parallel REVEAL operations using ThreadPoolExecutor.

    Args:
        workload: List of (start_idx, count) tuples from distribute_workload()
        bulkFlag: Boolean indicating bulk mode
        hostCRDP: CRDP server hostname
        c_data: Single ciphertext data (for discrete mode)
        c_data_array: Array of ciphertext data (for bulk mode)
        protectionPolicy: Protection policy name
        c_version: External version
        r_user: Username for reveal

    Returns:
        AggregatedMetrics, list of results
    """
    num_tasks = len(workload)
    total_items = sum(count for _, count in workload)

    # Create shared progress bar and lock
    progress_lock = Lock()
    desc = "Parallel REVEAL Progress"

    # Aggregated metrics
    agg_metrics = AggregatedMetrics()
    agg_metrics.overall_start = time.time()

    results = []

    with tqdm(total=total_items, desc=desc) as pbar:
        with ThreadPoolExecutor(max_workers=num_tasks) as executor:
            futures = {}

            # Submit workers based on mode
            if bulkFlag:
                # Bulk mode: divide c_data_array into chunks
                for task_id, (start_idx, count) in enumerate(workload):
                    data_chunk = c_data_array[start_idx:start_idx + count]
                    future = executor.submit(
                        worker_reveal_bulk,
                        task_id, data_chunk, hostCRDP, protectionPolicy, c_version, r_user, pbar, progress_lock
                    )
                    futures[future] = task_id
            else:
                # Discrete mode: each worker makes individual calls
                for task_id, (start_idx, count) in enumerate(workload):
                    future = executor.submit(
                        worker_reveal_discrete,
                        task_id, start_idx, count, hostCRDP, c_data, protectionPolicy, c_version, r_user, pbar, progress_lock
                    )
                    futures[future] = task_id

            # Collect results as workers complete
            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    if bulkFlag:
                        metrics, r_data_array = future.result()
                        results.append((task_id, metrics, r_data_array))
                    else:
                        metrics, r_data = future.result()
                        results.append((task_id, metrics, r_data))
                    agg_metrics.add_worker_metrics(metrics)
                except Exception as e:
                    print(colored(f"\nWorker {task_id} failed: {e}", "red"))

    agg_metrics.overall_end = time.time()

    return agg_metrics, results


def display_worker_performance(agg_metrics, operation_name):
    """
    Display per-worker performance table for a single phase.

    Args:
        agg_metrics: AggregatedMetrics object
        operation_name: "PROTECT" or "REVEAL"
    """
    if len(agg_metrics.worker_metrics) > 1:
        print(f"\n  {operation_name} Worker Performance:")
        for metrics in sorted(agg_metrics.worker_metrics, key=lambda m: m.worker_id):
            avg_time = (metrics.duration() / metrics.items_processed * 1000) if metrics.items_processed > 0 else 0
            print(f"    Worker {metrics.worker_id}: {metrics.items_processed} items in {metrics.duration():.2f}s "
                  f"(avg: {avg_time:.1f}ms/item)")
        print()


def display_test_summary(agg_metrics, data_size, operation_name, bulkFlag):
    """
    Display test completion summary with load distribution for a single phase.

    Args:
        agg_metrics: AggregatedMetrics object
        data_size: Total data size in bytes
        operation_name: "PROTECT" or "REVEAL"
        bulkFlag: Boolean indicating bulk mode
    """
    overall_time = agg_metrics.overall_duration()

    if bulkFlag:
        pRate = (data_size / overall_time) / 1000000  # MB/s
        outStr = (
            f"CRDP Test Completed - {operation_name}. "
            f"{data_size/1000000:.2f} MBs processed. "
            f"Process time: {overall_time:.2f} sec. "
            f"Rate: {pRate:.2f} MB/s."
        )
    else:
        pRate = data_size / overall_time  # B/s
        outStr = (
            f"CRDP Test Completed - {operation_name}. "
            f"{data_size} bytes processed. "
            f"Process time: {overall_time:.2f} sec. "
            f"Rate: {pRate:.2f} B/s."
        )

    print(colored(outStr, "green", attrs=["bold"]))

    # Display load distribution if multiple workers
    if len(agg_metrics.worker_metrics) > 1:
        min_dur = agg_metrics.min_worker_duration()
        max_dur = agg_metrics.max_worker_duration()
        avg_dur = agg_metrics.avg_worker_duration()
        skew = agg_metrics.load_skew_percent()

        print(f"  Load Distribution: Min: {min_dur:.2f}s | Max: {max_dur:.2f}s | "
              f"Avg: {avg_dur:.2f}s | Skew: {skew:.1f}%")

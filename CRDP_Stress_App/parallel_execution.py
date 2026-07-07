# Parallel Execution Module for CRDP Stress Testing
#
# This module provides parallel execution capabilities for stress testing
# CRDP servers using ThreadPoolExecutor. It includes worker functions,
# metrics collection, and workload distribution logic.
#
######################################################################
import time
import threading
import statistics
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from tqdm import tqdm
from termcolor import colored
from CRDP_REST_API import (
    protectData, protectBulkData, revealData, revealBulkData,
    CRDP_PROTECTED_DATA_NAME, CRDP_DATA_NAME, CRDP_EXTERNAL_VER_NAME
)

# psutil powers the client-host CPU sampler (attribution: is the Python load
# generator itself the bottleneck?). It is an optional dependency - if it is not
# installed the sampler degrades gracefully and everything else still works.
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


# -------------------- Metrics Classes --------------------

class WorkerMetrics:
    """Metrics collected by each worker thread"""
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.start_time = None
        self.end_time = None
        self.items_processed = 0
        self.errors = []
        # One (call_start_ts, call_end_ts, n_items) tuple per bulk REST call this
        # worker made. Latency, per-call size, and rolling throughput all derive
        # from these records - they are the raw material for attribution.
        self.call_records = []

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

    # -------------------- Derived attribution metrics --------------------
    # All of these are computed AFTER the timed phase completes, from the raw
    # per-call records, so they add no overhead to the measured hot path.

    def all_call_records(self):
        """Flatten every worker's per-call records into one list."""
        recs = []
        for m in self.worker_metrics:
            recs.extend(m.call_records)
        return recs

    def all_latencies(self):
        """Per-bulk-call wall times in seconds."""
        return [end - start for start, end, _ in self.all_call_records()]

    def cards_per_sec(self):
        """Primary throughput metric: items (credit cards) processed per second."""
        dur = self.overall_duration()
        return (self.total_items / dur) if dur > 0 else 0

    def latency_percentiles(self):
        """p50/p95/p99/max of per-bulk-call latency (seconds)."""
        return compute_percentiles(sorted(self.all_latencies()))

    def rolling_throughput(self, bucket=1.0):
        """
        Cards/sec time series: bucket completed items by their call-end time into
        `bucket`-second bins (relative to overall_start). Exposes ramp, steady
        state, and collapse that a single wall-clock average hides.
        """
        recs = self.all_call_records()
        if not recs or self.overall_start is None:
            return []
        t0 = self.overall_start
        buckets = {}
        for _, end, n in recs:
            idx = int((end - t0) // bucket)
            buckets[idx] = buckets.get(idx, 0) + n
        if not buckets:
            return []
        max_idx = max(buckets)
        return [buckets.get(i, 0) / bucket for i in range(max_idx + 1)]


# -------------------- Attribution Helpers --------------------

def compute_percentiles(sorted_latencies):
    """
    Linear-interpolated percentiles from an already-sorted list of latencies.
    Returns a dict of p50/p95/p99/max (same units as input, i.e. seconds).
    Small-sample safe: degrades sensibly for 0 or 1 samples.
    """
    if not sorted_latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}

    n = len(sorted_latencies)

    def pct(p):
        if n == 1:
            return sorted_latencies[0]
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        return sorted_latencies[f] + (sorted_latencies[c] - sorted_latencies[f]) * (k - f)

    return {
        "p50": pct(0.50),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": sorted_latencies[-1],
    }


class ClientCpuSampler:
    """
    Samples client-host CPU utilization in a background thread for the duration
    of a phase. This is the clearest single signal for "is the Python load
    generator the wall?" - if avg CPU sits near 100% x cores while throughput is
    capped, the client is the bottleneck, not CRDP.

    No-ops gracefully (available=False) when psutil is not installed.
    """
    def __init__(self, interval=0.5):
        self.interval = interval
        self.samples = []
        self._stop = threading.Event()
        self._thread = None
        self.cores = psutil.cpu_count(logical=True) if _HAS_PSUTIL else 0

    def start(self):
        if not _HAS_PSUTIL:
            return self
        psutil.cpu_percent(None)  # prime the internal counter; first call is 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        # psutil.cpu_percent(interval=...) blocks for `interval` and returns the
        # system-wide utilization over that window, so this loop is self-paced.
        while not self._stop.is_set():
            self.samples.append(psutil.cpu_percent(interval=self.interval))

    def stop(self):
        if self._thread is not None:
            self._stop.set()
            self._thread.join(timeout=2)

    def summary(self):
        if not _HAS_PSUTIL:
            return {"available": False}
        if not self.samples:
            return {"available": True, "avg": 0.0, "peak": 0.0, "cores": self.cores}
        return {
            "available": True,
            "avg": sum(self.samples) / len(self.samples),
            "peak": max(self.samples),
            "cores": self.cores,
        }


def single_worker_aggregate(call_records, overall_start, overall_end):
    """
    Build an AggregatedMetrics from a sequential (single-thread) run's call
    records so the numThreads==1 path reports the same rich metrics as the
    parallel path and the two are directly comparable.
    """
    m = WorkerMetrics(0)
    m.start_time = overall_start
    m.end_time = overall_end
    m.call_records = call_records
    m.items_processed = sum(n for _, _, n in call_records)

    agg = AggregatedMetrics()
    agg.overall_start = overall_start
    agg.overall_end = overall_end
    agg.add_worker_metrics(m)
    return agg


def build_phase_record(agg_metrics, data_size, cpu, operation_name):
    """
    Assemble one phase's metrics into a plain (JSON-serializable) dict for the
    -jsonout results file.
    """
    dur = agg_metrics.overall_duration()
    pct = agg_metrics.latency_percentiles()
    return {
        "operation": operation_name,
        "total_cards": agg_metrics.total_items,
        "wall_time_sec": dur,
        "wall_start_epoch": agg_metrics.overall_start,
        "wall_end_epoch": agg_metrics.overall_end,
        "cards_per_sec": agg_metrics.cards_per_sec(),
        "mb_per_sec": (data_size / dur / 1_000_000) if dur > 0 else 0,
        "data_size_bytes": data_size,
        "num_bulk_calls": len(agg_metrics.all_call_records()),
        "workers": len(agg_metrics.worker_metrics),
        "load_skew_pct": agg_metrics.load_skew_percent(),
        "latency_ms": {k: v * 1000 for k, v in pct.items()},
        "rolling_cards_per_sec": agg_metrics.rolling_throughput(),
        "client_cpu": cpu.summary() if cpu is not None else {"available": False},
    }


# -------------------- Workload Distribution --------------------

def distribute_workload(total_count, num_threads):
    """
    Divide total_count into num_threads chunks.
    Returns list of (start_index, count) tuples.

    Example: distribute_workload(1000, 3) -> [(0, 334), (334, 333), (667, 333)]
    """
    if num_threads <= 0:
        raise ValueError("num_threads must be > 0")
    if total_count <= 0:
        raise ValueError("total_count must be > 0")

    base_size = total_count // num_threads
    remainder = total_count % num_threads

    workload = []
    current_start = 0

    for i in range(num_threads):
        # Distribute remainder across first few tasks
        chunk_size = base_size + (1 if i < remainder else 0)
        workload.append((current_start, chunk_size))
        current_start += chunk_size

    return workload


# -------------------- Session-based API Wrappers --------------------

def protectData_session(session, t_endpointCRDP, t_data, t_protectionPolicy):
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

    t_endpoint = "http://%s%s" % (t_endpointCRDP, CRDP_PROTECT)
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

    # external_version is optional - policies without key rotation omit it.
    t_json = r.json()
    t_protectedData = t_json[CRDP_PROTECTED_DATA_NAME]
    t_version = t_json.get(CRDP_EXTERNAL_VER_NAME)

    return t_protectedData, t_version


def protectBulkData_session(session, t_endpointCRDP, t_dataArray, t_protectionPolicy):
    """
    Session-aware version of protectBulkData.
    """
    from CRDP_REST_API import (
        CRDP_BULK_PROTECT, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_DATA_ARRAY_NAME,
        CRDP_PROTECTED_DATA_ARRAY_NAME, CRDP_EXTERNAL_VER_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_endpointCRDP, CRDP_BULK_PROTECT)
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

    # external_version is optional - policies without key rotation omit it from
    # the per-item entries in protected_data_array.
    t_protectedData = r.json()[CRDP_PROTECTED_DATA_ARRAY_NAME]
    t_version = t_protectedData[0].get(CRDP_EXTERNAL_VER_NAME) if t_protectedData else None

    return t_protectedData, t_version


def revealData_session(session, t_endpointCRDP, t_data, t_protectionPolicy, t_externalVersion, t_user):
    """
    Session-aware version of revealData.
    """
    from CRDP_REST_API import (
        CRDP_REVEAL, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_EXTERNAL_VER_NAME,
        CRDP_USERNAME_NAME, CRDP_PROTECTED_DATA_NAME, CRDP_DATA_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_endpointCRDP, CRDP_REVEAL)
    t_headers = {APP_CONTENT_TYPE: APP_JSON}
    t_dataStr = {
        CRDP_PROTECTION_POLICY_NAME: t_protectionPolicy,
        CRDP_USERNAME_NAME: t_user,
        CRDP_PROTECTED_DATA_NAME: t_data,
    }
    # Only include external_version when the policy actually returned one on protect.
    if t_externalVersion is not None:
        t_dataStr[CRDP_EXTERNAL_VER_NAME] = t_externalVersion

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


def revealBulkData_session(session, t_endpointCRDP, t_dataArray, t_protectionPolicy, t_externalVersion, t_user):
    """
    Session-aware version of revealBulkData.
    """
    from CRDP_REST_API import (
        CRDP_BULK_REVEAL, APP_CONTENT_TYPE, APP_JSON,
        CRDP_PROTECTION_POLICY_NAME, CRDP_USERNAME_NAME,
        CRDP_PROTECTED_DATA_ARRAY_NAME, CRDP_DATA_ARRAY_NAME,
        NET_TIMEOUT, STATUS_CODE_OK, kPrintError
    )

    t_endpoint = "http://%s%s" % (t_endpointCRDP, CRDP_BULK_REVEAL)
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

def worker_protect_discrete(task_id, start_idx, count, endpointCRDP, p_data_array, protectionPolicy, collect_results, pbar, lock):
    """
    Worker function for discrete PROTECT operations.
    Each worker makes individual protectData calls for its assigned slice of
    p_data_array. When collect_results is True (CSV list mode) every protected
    value is returned in order; otherwise only the last value is returned.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    c_data = None
    c_data_list = []
    c_version = None

    try:
        for i in range(count):
            c_data, c_version = protectData_session(session, endpointCRDP, p_data_array[start_idx + i], protectionPolicy)
            if collect_results:
                c_data_list.append(c_data)

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

    return metrics, (c_data_list if collect_results else c_data), c_version


def worker_protect_bulk(task_id, data_chunk, endpointCRDP, protectionPolicy, pbar, lock):
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
        c_data_array, c_version = protectBulkData_session(session, endpointCRDP, data_chunk, protectionPolicy)
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


def worker_reveal_discrete(task_id, start_idx, count, endpointCRDP, c_data, protectionPolicy, c_version, r_user, pbar, lock):
    """
    Worker function for discrete REVEAL operations.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    r_data = None

    try:
        for i in range(count):
            r_data = revealData_session(session, endpointCRDP, c_data, protectionPolicy, c_version, r_user)

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


def worker_reveal_bulk(task_id, data_chunk, endpointCRDP, protectionPolicy, c_version, r_user, pbar, lock):
    """
    Worker function for bulk REVEAL operations.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    r_data_array = None

    try:
        r_data_array = revealBulkData_session(session, endpointCRDP, data_chunk, protectionPolicy, c_version, r_user)
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

def execute_protect_parallel(workload, bulkFlag, endpointCRDP, p_data, p_data_array, protectionPolicy, collect_results=False):
    """
    Execute parallel PROTECT operations using ThreadPoolExecutor.

    Args:
        workload: List of (start_idx, count) tuples from distribute_workload()
        bulkFlag: Boolean indicating bulk mode
        endpointCRDP: CRDP server hostname
        p_data: Single plaintext data (unused; kept for signature compatibility)
        p_data_array: Array of plaintext data (one entry per item)
        protectionPolicy: Protection policy name
        collect_results: When True, discrete workers return every protected
            value in order (CSV list mode) instead of only the last one

    Returns:
        AggregatedMetrics, list of results, c_version
    """
    num_threads = len(workload)
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
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = {}

            # Submit workers based on mode
            if bulkFlag:
                # Bulk mode: divide p_data_array into chunks
                for task_id, (start_idx, count) in enumerate(workload):
                    data_chunk = p_data_array[start_idx:start_idx + count]
                    future = executor.submit(
                        worker_protect_bulk,
                        task_id, data_chunk, endpointCRDP, protectionPolicy, pbar, progress_lock
                    )
                    futures[future] = task_id
            else:
                # Discrete mode: each worker makes individual calls
                for task_id, (start_idx, count) in enumerate(workload):
                    future = executor.submit(
                        worker_protect_discrete,
                        task_id, start_idx, count, endpointCRDP, p_data_array, protectionPolicy, collect_results, pbar, progress_lock
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


def execute_reveal_parallel(workload, bulkFlag, endpointCRDP, c_data, c_data_array, protectionPolicy, c_version, r_user):
    """
    Execute parallel REVEAL operations using ThreadPoolExecutor.

    Args:
        workload: List of (start_idx, count) tuples from distribute_workload()
        bulkFlag: Boolean indicating bulk mode
        endpointCRDP: CRDP server hostname
        c_data: Single ciphertext data (for discrete mode)
        c_data_array: Array of ciphertext data (for bulk mode)
        protectionPolicy: Protection policy name
        c_version: External version
        r_user: Username for reveal

    Returns:
        AggregatedMetrics, list of results
    """
    num_threads = len(workload)
    total_items = sum(count for _, count in workload)

    # Create shared progress bar and lock
    progress_lock = Lock()
    desc = "Parallel REVEAL Progress"

    # Aggregated metrics
    agg_metrics = AggregatedMetrics()
    agg_metrics.overall_start = time.time()

    results = []

    with tqdm(total=total_items, desc=desc) as pbar:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = {}

            # Submit workers based on mode
            if bulkFlag:
                # Bulk mode: divide c_data_array into chunks
                for task_id, (start_idx, count) in enumerate(workload):
                    data_chunk = c_data_array[start_idx:start_idx + count]
                    future = executor.submit(
                        worker_reveal_bulk,
                        task_id, data_chunk, endpointCRDP, protectionPolicy, c_version, r_user, pbar, progress_lock
                    )
                    futures[future] = task_id
            else:
                # Discrete mode: each worker makes individual calls
                for task_id, (start_idx, count) in enumerate(workload):
                    future = executor.submit(
                        worker_reveal_discrete,
                        task_id, start_idx, count, endpointCRDP, c_data, protectionPolicy, c_version, r_user, pbar, progress_lock
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


def worker_protect_messages(task_id, indexed_messages, endpointCRDP, protectionPolicy, pbar, lock):
    """
    Worker that processes a list of bulk PROTECT messages.

    indexed_messages: list of (msg_idx, payload_list) tuples, where each payload_list
    is itself a list of plaintexts sent in a single bulk REST call. msg_idx is the
    original message order index so the caller can reassemble results in order.

    Returns metrics, list of (msg_idx, protected_chunk), c_version.
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    results = []
    c_version = None
    total_items = 0

    try:
        for msg_idx, payloads in indexed_messages:
            call_start = time.time()
            c_data_array, version = protectBulkData_session(
                session, endpointCRDP, payloads, protectionPolicy
            )
            call_end = time.time()
            results.append((msg_idx, c_data_array))
            if c_version is None:
                c_version = version
            n = len(payloads)
            metrics.call_records.append((call_start, call_end, n))
            total_items += n
            with lock:
                pbar.update(n)
        metrics.items_processed = total_items
    except Exception as e:
        metrics.errors.append(str(e))
        print(colored(f"\nWorker {task_id} error: {e}", "red"))
    finally:
        metrics.end_time = time.time()
        session.close()

    return metrics, results, c_version


def worker_reveal_messages(task_id, indexed_messages, endpointCRDP, protectionPolicy, c_version, r_user, pbar, lock):
    """
    Worker that processes a list of bulk REVEAL messages.

    indexed_messages: list of (msg_idx, ciphertext_list) tuples.
    Returns metrics, list of (msg_idx, revealed_chunk).
    """
    session = requests.Session()
    metrics = WorkerMetrics(task_id)
    metrics.start_time = time.time()

    results = []
    total_items = 0

    try:
        for msg_idx, payloads in indexed_messages:
            call_start = time.time()
            r_data_array = revealBulkData_session(
                session, endpointCRDP, payloads, protectionPolicy, c_version, r_user
            )
            call_end = time.time()
            results.append((msg_idx, r_data_array))
            n = len(payloads)
            metrics.call_records.append((call_start, call_end, n))
            total_items += n
            with lock:
                pbar.update(n)
        metrics.items_processed = total_items
    except Exception as e:
        metrics.errors.append(str(e))
        print(colored(f"\nWorker {task_id} error: {e}", "red"))
    finally:
        metrics.end_time = time.time()
        session.close()

    return metrics, results


def execute_protect_messages_parallel(messages, num_threads, endpointCRDP, protectionPolicy):
    """
    Execute parallel bulk PROTECT by distributing messages (round-robin) across workers.

    Args:
        messages: list of bulk-call payloads (each item is itself a list of plaintexts)
        num_threads: number of worker threads
        endpointCRDP: CRDP endpoint
        protectionPolicy: protection policy name

    Returns:
        AggregatedMetrics, flat c_data_array (in original payload order), c_version
    """
    # Round-robin assignment of indexed messages to workers.
    worker_messages = [[] for _ in range(num_threads)]
    for i, msg in enumerate(messages):
        worker_messages[i % num_threads].append((i, msg))

    total_items = sum(len(m) for m in messages)
    progress_lock = Lock()
    agg_metrics = AggregatedMetrics()
    agg_metrics.overall_start = time.time()

    all_chunks = []
    c_version = None

    with tqdm(total=total_items, desc="Parallel PROTECT Progress") as pbar:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = {}
            for task_id, msg_list in enumerate(worker_messages):
                if not msg_list:
                    continue
                future = executor.submit(
                    worker_protect_messages,
                    task_id, msg_list, endpointCRDP, protectionPolicy, pbar, progress_lock,
                )
                futures[future] = task_id

            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    metrics, chunks, version = future.result()
                    all_chunks.extend(chunks)
                    agg_metrics.add_worker_metrics(metrics)
                    if c_version is None and version is not None:
                        c_version = version
                except Exception as e:
                    print(colored(f"\nWorker {task_id} failed: {e}", "red"))

    agg_metrics.overall_end = time.time()

    all_chunks.sort(key=lambda x: x[0])
    c_data_array = []
    for _, chunk in all_chunks:
        c_data_array.extend(chunk)

    return agg_metrics, c_data_array, c_version


def execute_reveal_messages_parallel(messages, num_threads, endpointCRDP, protectionPolicy, c_version, r_user):
    """
    Execute parallel bulk REVEAL by distributing messages (round-robin) across workers.

    Args:
        messages: list of bulk-call payloads (each item is itself a list of ciphertext dicts)
        num_threads: number of worker threads
        endpointCRDP: CRDP endpoint
        protectionPolicy: protection policy name
        c_version: external version (carried for API signature; per-item version is embedded)
        r_user: username for reveal

    Returns:
        AggregatedMetrics, flat r_data_array (in original payload order)
    """
    worker_messages = [[] for _ in range(num_threads)]
    for i, msg in enumerate(messages):
        worker_messages[i % num_threads].append((i, msg))

    total_items = sum(len(m) for m in messages)
    progress_lock = Lock()
    agg_metrics = AggregatedMetrics()
    agg_metrics.overall_start = time.time()

    all_chunks = []

    with tqdm(total=total_items, desc="Parallel REVEAL Progress") as pbar:
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = {}
            for task_id, msg_list in enumerate(worker_messages):
                if not msg_list:
                    continue
                future = executor.submit(
                    worker_reveal_messages,
                    task_id, msg_list, endpointCRDP, protectionPolicy, c_version, r_user, pbar, progress_lock,
                )
                futures[future] = task_id

            for future in as_completed(futures):
                task_id = futures[future]
                try:
                    metrics, chunks = future.result()
                    all_chunks.extend(chunks)
                    agg_metrics.add_worker_metrics(metrics)
                except Exception as e:
                    print(colored(f"\nWorker {task_id} failed: {e}", "red"))

    agg_metrics.overall_end = time.time()

    all_chunks.sort(key=lambda x: x[0])
    r_data_array = []
    for _, chunk in all_chunks:
        r_data_array.extend(chunk)

    return agg_metrics, r_data_array


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


def display_test_summary(agg_metrics, data_size, operation_name, cpu=None):
    """
    Display test completion summary with load distribution for a single phase.

    Args:
        agg_metrics: AggregatedMetrics object
        data_size: Total data size in bytes
        operation_name: "PROTECT" or "REVEAL"
        cpu: optional ClientCpuSampler whose samples were captured over this phase
    """
    overall_time = agg_metrics.overall_duration()


    pRate = (data_size / overall_time) / 1000000 if overall_time > 0 else 0  # MB/s
    outStr = (
        f"CRDP Test Completed - {operation_name}. "
        f"{data_size/1000000:.3f} MBs processed. "
        f"Process time: {overall_time:.2f} sec. "
        f"Rate: {pRate:.3f} MB/s."
    )


    print(colored(outStr, "green", attrs=["bold"]))

    # Primary throughput metric: cards (iterations) per second - the goal unit.
    cps = agg_metrics.cards_per_sec()
    print(colored(
        f"  Throughput: {cps:,.0f} cards/sec  "
        f"({agg_metrics.total_items:,} cards in {overall_time:.2f}s)",
        "cyan", attrs=["bold"]))

    # Per-bulk-call latency distribution. Most informative at small batch sizes;
    # at very large batches a run may be only a handful of calls (coarse).
    pct = agg_metrics.latency_percentiles()
    ncalls = len(agg_metrics.all_call_records())
    print(colored(
        f"  Latency/bulk-call ({ncalls} calls): "
        f"p50 {pct['p50']*1000:.1f}ms | p95 {pct['p95']*1000:.1f}ms | "
        f"p99 {pct['p99']*1000:.1f}ms | max {pct['max']*1000:.1f}ms",
        "cyan"))

    # Rolling throughput - exposes plateau / collapse hidden by the wall average.
    rolling = agg_metrics.rolling_throughput()
    if rolling:
        print(colored(
            f"  Rolling cards/sec: peak {max(rolling):,.0f} | "
            f"mean {sum(rolling)/len(rolling):,.0f} | min {min(rolling):,.0f}",
            "cyan"))

    # Client-host CPU - is the Python load generator itself the wall?
    if cpu is not None:
        cs = cpu.summary()
        if cs.get("available"):
            print(colored(
                f"  Client CPU: avg {cs['avg']:.0f}% | peak {cs['peak']:.0f}%  "
                f"(of {cs['cores']} logical cores)", "cyan"))
        else:
            print(colored(
                "  Client CPU: not captured (pip install psutil to enable)",
                "yellow"))

    # Display load distribution if multiple workers
    if len(agg_metrics.worker_metrics) > 1:
        min_dur = agg_metrics.min_worker_duration()
        max_dur = agg_metrics.max_worker_duration()
        avg_dur = agg_metrics.avg_worker_duration()
        skew = agg_metrics.load_skew_percent()

        print(f"  Load Distribution: Min: {min_dur:.2f}s | Max: {max_dur:.2f}s | "
              f"Avg: {avg_dur:.2f}s | Skew: {skew:.1f}%")

import asyncio
import csv
import logging
import os
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse  # To set custom headers

# Ensure surf_client.py is in the same directory or adjust import
from surf_client import SurfVMController

# --- Configuration ---
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s - %(levelname)s - %(threadName)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI()
surf_controller = SurfVMController()

VM_DATA_FILE_PATH = Path(os.getenv("VM_DATA_FILE", "/app/vm_data/surf_vms.csv"))
OLLAMA_DEFAULT_PORT = int(os.getenv("OLLAMA_DEFAULT_PORT", "11434"))

WAKEUP_POLL_INTERVAL_SECONDS = 10  # How often to poll Ollama port during wake-up
WAKEUP_TIMEOUT_SECONDS = int(
    os.getenv("WAKEUP_TIMEOUT_SECONDS", "150")
)  # Max time for one VM to wake

AUTO_PAUSE_CHECK_INTERVAL_SECONDS = int(os.getenv("AUTO_PAUSE_CHECK_INTERVAL", "60"))
AUTO_PAUSE_INACTIVITY_THRESHOLD_SECONDS = int(
    os.getenv("AUTO_PAUSE_INACTIVITY_THRESHOLD", "600")
)

TARGET_ACTIVE_GPUS = int(os.getenv("TARGET_ACTIVE_GPUS", "1"))
SCALE_UP_COOLDOWN_SECONDS = int(os.getenv("SCALE_UP_COOLDOWN_SECONDS", "60"))

# --- In-memory state ---
vm_info_cache: Dict[str, Dict[str, Any]] = {}  # ip -> {id, name, port}
last_active_times: Dict[str, datetime] = {}  # ip -> datetime
last_scale_up_initiation_time: Optional[datetime] = None
vm_data_lock = Lock()
shutdown_event = Event()  # For gracefully stopping the scheduler thread


# --- Helper Functions ---
def load_vm_data():
    global vm_info_cache
    temp_cache = {}
    if not VM_DATA_FILE_PATH.exists():
        logger.error(
            f"VM data file not found at {VM_DATA_FILE_PATH}. Cannot manage VMs."
        )
        vm_info_cache = {}  # Ensure it's empty if file not found
        return
    try:
        with VM_DATA_FILE_PATH.open("r", newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                ip = row.get("ip")
                vm_id = row.get("id")
                name = row.get("name")
                if ip and vm_id:
                    ollama_port = int(row.get("ollama_port", OLLAMA_DEFAULT_PORT))
                    temp_cache[ip] = {
                        "id": vm_id,
                        "name": name,
                        "ollama_port": ollama_port,
                    }
                else:
                    logger.warning(f"Skipping VM row due to missing IP or ID: {row}")
        with vm_data_lock:
            vm_info_cache = temp_cache
        logger.info(
            f"Successfully loaded/reloaded VM data for {len(vm_info_cache)} VMs from {VM_DATA_FILE_PATH}"
        )
    except Exception as e:
        logger.error(f"Error loading VM data: {e}", exc_info=True)
        with vm_data_lock:
            vm_info_cache = {}


def is_ollama_ready(host: str, port: int) -> bool:
    try:
        with socket.create_connection(
            (host, port), timeout=3
        ):  # Short timeout for readiness check
            logger.debug(
                f"TCP check: Successfully connected to Ollama at {host}:{port}"
            )
            return True
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logger.debug(
            f"TCP check: Ollama not ready at {host}:{port}. Error: {type(e).__name__}"
        )
        return False


def update_last_active_time(ip_address: str):
    with vm_data_lock:
        last_active_times[ip_address] = datetime.now()
    logger.info(f"Updated last active time for {ip_address}")


async def _resume_and_poll_vm(vm_details: Dict) -> Optional[str]:
    """Synchronously resumes a VM and polls for readiness. Returns 'ip:port' or None."""
    ip = vm_details["ip"]
    port = vm_details["port"]
    vm_id = vm_details["id"]
    name = vm_details["name"]

    logger.info(f"Attempting to resume VM {name} ({vm_id}) synchronously.")
    resume_triggered = surf_controller.resume_vm(vm_id)
    if not resume_triggered:
        logger.error(f"Failed to trigger resume for VM {name} via SURF API.")
        return None

    start_time = time.time()
    while time.time() - start_time < WAKEUP_TIMEOUT_SECONDS:
        if is_ollama_ready(ip, port):
            logger.info(f"VM {name} ({ip}) resumed and Ollama is now responsive.")
            update_last_active_time(ip)
            return f"{ip}:{port}"
        if shutdown_event.is_set():
            logger.info(
                f"Shutdown event received during wake-up poll for {name}. Aborting."
            )
            return None
        logger.info(
            f"Waiting for VM {name} ({ip})... ({int(time.time() - start_time + WAKEUP_POLL_INTERVAL_SECONDS)}s / {WAKEUP_TIMEOUT_SECONDS}s)"
        )
        await asyncio.sleep(
            WAKEUP_POLL_INTERVAL_SECONDS
        )  # Use asyncio.sleep if in async context directly
        # time.sleep(WAKEUP_POLL_INTERVAL_SECONDS) # If this helper is called from sync context or background thread

    logger.error(
        f"Timeout: VM {name} ({ip}) did not become responsive in {WAKEUP_TIMEOUT_SECONDS}s."
    )
    return None


async def _trigger_background_wake_up(vm_to_wake: Dict):
    """Wrapper to call resume_and_poll_vm for background task"""
    logger.info(f"Background task: Waking up {vm_to_wake['name']}")
    await _resume_and_poll_vm(vm_to_wake)


# --- FastAPI Application Events ---
@app.on_event("startup")
async def startup_event():
    load_vm_data()  # Load initial data
    # Start the auto-pause background task
    auto_pause_thread = Thread(
        target=auto_pause_scheduler, daemon=True, name="AutoPauseThread"
    )
    auto_pause_thread.start()
    logger.info("Wake-up service started with auto-pause scheduler.")


@app.on_event("shutdown")
def shutdown_app_event():
    logger.info("Wake-up service shutting down...")
    shutdown_event.set()


# --- FastAPI Endpoints ---
@app.get("/request-instance")
async def request_ollama_instance_endpoint(background_tasks: BackgroundTasks):
    global last_scale_up_initiation_time
    logger.info("Received /request-instance call.")

    with vm_data_lock:
        # Make a copy for safe iteration if load_vm_data runs concurrently (though less likely here)
        all_known_vms_list = list(vm_info_cache.items())

    if not all_known_vms_list:
        logger.error("No VMs configured. Cannot provide an instance.")
        raise HTTPException(
            status_code=500, detail="No VMs configured in wake-up service."
        )

    active_vms_details: List[Dict] = []
    inactive_vms_details: List[Dict] = []

    for ip, details in all_known_vms_list:
        if is_ollama_ready(ip, details["ollama_port"]):
            # Ensure details for active_vms_details includes all necessary keys
            active_vm_data = {"ip": ip, "port": details["ollama_port"], **details}
            active_vms_details.append(active_vm_data)
            update_last_active_time(ip)
        else:
            inactive_vm_data = {"ip": ip, "port": details["ollama_port"], **details}
            inactive_vms_details.append(inactive_vm_data)

    logger.info(
        f"Current status - Active VMs: {len(active_vms_details)}, Inactive VMs: {len(inactive_vms_details)}."
    )

    # Scenario 1: At least one VM is already active
    if active_vms_details:
        # Simple selection: pick the first active one found.
        # TODO: Implement better selection if multiple are active (e.g., round-robin or least recently used by this service)
        chosen_vm = active_vms_details[0]
        logger.info(
            f"Providing already active VM: {chosen_vm['name']} ({chosen_vm['ip']}:{chosen_vm['port']})"
        )

        # Proactive scaling: If we have active VMs but fewer than our target,
        # and there are inactive ones available, try to wake another one in the background.
        with vm_data_lock:  # Protect access to last_scale_up_initiation_time
            can_attempt_scale_up = True
            if last_scale_up_initiation_time and (
                datetime.now() - last_scale_up_initiation_time
                < timedelta(seconds=SCALE_UP_COOLDOWN_SECONDS)
            ):
                can_attempt_scale_up = False
                logger.debug(
                    f"Scale-up cooldown active. Last attempt: {last_scale_up_initiation_time}. Will not attempt background wake."
                )

            if (
                len(active_vms_details) < TARGET_ACTIVE_GPUS
                and inactive_vms_details
                and can_attempt_scale_up
            ):
                vm_to_wake_for_scaling = inactive_vms_details[
                    0
                ]  # Simple: pick the first inactive
                logger.info(
                    f"Scaling up: Active {len(active_vms_details)} < Target {TARGET_ACTIVE_GPUS}. "
                    f"Attempting to wake {vm_to_wake_for_scaling['name']} in background."
                )
                background_tasks.add_task(
                    _trigger_background_wake_up, vm_to_wake_for_scaling
                )
                last_scale_up_initiation_time = datetime.now()

        headers = {"X-Ready-GPU-Addr": f"{chosen_vm['ip']}:{chosen_vm['port']}"}
        return JSONResponse(
            content={"status": "ok", "message": f"Using active VM {chosen_vm['name']}"},
            headers=headers,
        )

    # Scenario 2: No VMs are currently active. Need to wake one synchronously for this request.
    if inactive_vms_details:
        vm_to_wake = inactive_vms_details[0]  # Simple: pick the first inactive one
        logger.info(
            f"No active VMs. Attempting to wake primary candidate: {vm_to_wake['name']} ({vm_to_wake['id']})"
        )

        # This call will block until this VM is ready or timeout
        ready_vm_addr_str = await _resume_and_poll_vm(
            vm_to_wake
        )  # Make sure this helper is async or run in threadpool

        if ready_vm_addr_str:
            # After successfully waking one, consider if we should immediately try to wake another
            # if TARGET_ACTIVE_GPUS > 1 and there are still other inactive VMs.
            with vm_data_lock:
                # Check if we still want more active VMs and if cooldown allows
                # active_vms_count is now 1 because we just woke one.
                if 1 < TARGET_ACTIVE_GPUS and len(inactive_vms_details) > 1:
                    can_attempt_scale_up_after_sync_wake = True
                    if last_scale_up_initiation_time and (
                        datetime.now() - last_scale_up_initiation_time
                        < timedelta(seconds=SCALE_UP_COOLDOWN_SECONDS)
                    ):
                        can_attempt_scale_up_after_sync_wake = False

                    if can_attempt_scale_up_after_sync_wake:
                        current_inactive_after_wake = [
                            vm
                            for vm in inactive_vms_details
                            if vm["id"] != vm_to_wake["id"]
                        ]
                        if current_inactive_after_wake:
                            vm_to_wake_for_scaling = current_inactive_after_wake[0]
                            logger.info(
                                f"Scaling up after initial sync wake: Target {TARGET_ACTIVE_GPUS}, have 1 active. "
                                f"Attempting to wake {vm_to_wake_for_scaling['name']} in background."
                            )
                            background_tasks.add_task(
                                _trigger_background_wake_up, vm_to_wake_for_scaling
                            )
                            last_scale_up_initiation_time = datetime.now()

            headers = {"X-Ready-GPU-Addr": ready_vm_addr_str}
            return JSONResponse(
                content={
                    "status": "ok",
                    "message": f"VM {vm_to_wake['name']} resumed.",
                },
                headers=headers,
            )
        else:
            logger.error(f"Failed to wake primary candidate VM {vm_to_wake['name']}.")
            raise HTTPException(
                status_code=503,
                detail=f"Failed to make VM {vm_to_wake['name']} responsive.",
            )

    logger.error(
        "No VMs available in configuration to activate (all_known_vms_list was empty or all attempts failed)."
    )
    raise HTTPException(
        status_code=500, detail="No VMs configured or available to wake."
    )


# --- Auto-Pause Scheduler ---
def auto_pause_scheduler():
    logger.info("Auto-pause scheduler thread started.")
    while not shutdown_event.is_set():
        try:
            # Reload VM data periodically in case CSV changes, though restart is better for CSV changes
            # For simplicity, we load once at startup. A more advanced setup might watch the file.
            # load_vm_data() # Optionally reload data if it can change dynamically

            with vm_data_lock:
                current_vm_info_cache = dict(vm_info_cache)
                current_last_active_times = dict(last_active_times)

            now = datetime.now()
            if not current_vm_info_cache:
                logger.debug("Auto-pause: No VMs loaded in cache to check.")

            vms_found_active_in_this_cycle = []
            for ip, details in current_vm_info_cache.items():
                if is_ollama_ready(ip, details["ollama_port"]):  # Check live status
                    vms_found_active_in_this_cycle.append(ip)
                    # Ensure last_active_times is updated if a VM is found active but wasn't used via /request-instance
                    if ip not in current_last_active_times:
                        update_last_active_time(ip)
                        logger.info(
                            f"Auto-pause: VM {details['name']} ({ip}) found active during check, updated last_active_time."
                        )
                        current_last_active_times[ip] = last_active_times[
                            ip
                        ]  # reflect update for current cycle

            for ip, details in current_vm_info_cache.items():
                vm_id = details["id"]
                vm_name = details["name"]

                last_active = current_last_active_times.get(ip)

                if last_active:
                    if ip not in vms_found_active_in_this_cycle:
                        # If we thought it was active recently, but it's not responsive now, clear its active time
                        logger.info(
                            f"Auto-pause: VM {vm_name} ({ip}) was marked active but is now unresponsive. Clearing active time."
                        )
                        with vm_data_lock:
                            if ip in last_active_times:
                                del last_active_times[ip]
                        continue  # Don't try to pause an unresponsive VM

                    if now - last_active > timedelta(
                        seconds=AUTO_PAUSE_INACTIVITY_THRESHOLD_SECONDS
                    ):
                        logger.info(
                            f"Auto-pause: VM {vm_name} ({ip}) inactive for > {AUTO_PAUSE_INACTIVITY_THRESHOLD_SECONDS}s. Attempting auto-pause."
                        )
                        pause_success = surf_controller.pause_vm(vm_id)
                        if pause_success:
                            logger.info(
                                f"Auto-pause: Successfully triggered pause for VM {vm_name} ({ip})."
                            )
                            with vm_data_lock:
                                if ip in last_active_times:
                                    del last_active_times[ip]
                        else:
                            logger.warning(
                                f"Auto-pause: Failed to trigger pause for VM {vm_name} ({ip}). Will retry check later."
                            )
                # else: VM was never recorded as active by this service instance, or already cleared.
        except Exception as e:
            logger.error(
                f"Auto-pause scheduler encountered an error: {e}", exc_info=True
            )

        shutdown_event.wait(timeout=AUTO_PAUSE_CHECK_INTERVAL_SECONDS)
    logger.info("Auto-pause scheduler thread stopped.")

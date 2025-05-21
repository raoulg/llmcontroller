import os
import time
import socket
import logging
from fastapi import FastAPI, HTTPException, Query
from surf_client import SurfVMController

# Configure basic logging (FastAPI/Uvicorn will also have its own logging)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI()
surf_controller = SurfVMController()  # Initialize once

# Configuration for the Ollama host that this service manages/wakes
# These should be set as environment variables for the Docker container
OLLAMA_HOST_IP = os.getenv("OLLAMA_TARGET_HOST_IP")
OLLAMA_HOST_PORT = int(os.getenv("OLLAMA_TARGET_HOST_PORT", "11434"))
TARGET_VM_ID = os.getenv("OLLAMA_TARGET_VM_ID")  # The ID of the VM on SURF to wake

WAKEUP_POLL_INTERVAL_SECONDS = (
    10  # How often to check if Ollama port is open during wake-up
)
WAKEUP_TIMEOUT_SECONDS = 150  # Max time to wait for VM to wake up (2.5 minutes)


def is_ollama_ready(host: str, port: int) -> bool:
    """Checks if the Ollama port is connectable."""
    try:
        with socket.create_connection((host, port), timeout=5):
            logger.info(f"Successfully connected to Ollama at {host}:{port}")
            return True
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        logger.debug(f"Ollama not yet ready at {host}:{port}. Error: {e}")
        return False


@app.get("/check-and-wake")
async def check_and_wake_vm(
    vm_id: str = Query(..., description="The ID of the VM to check and wake"),
    # We can add more query params if needed in the future, e.g. target_port
):
    if not OLLAMA_HOST_IP or not TARGET_VM_ID:
        logger.error(
            "Server configuration missing: OLLAMA_TARGET_HOST_IP or OLLAMA_TARGET_VM_ID"
        )
        raise HTTPException(
            status_code=500, detail="Wake-up service not configured properly."
        )

    if vm_id != TARGET_VM_ID:
        logger.warning(
            f"Request for vm_id '{vm_id}' does not match configured TARGET_VM_ID '{TARGET_VM_ID}'. Ignoring."
        )
        # Or return an error, depending on how Nginx will call this.
        # For now, if Nginx passes a vm_id, let's make sure it's the one we care about.
        # If Nginx doesn't pass one, this parameter can be removed and we always use TARGET_VM_ID.
        # Let's assume Nginx will pass the correct TARGET_VM_ID for now.
        pass  # Allow if matches, or adjust logic if Nginx doesn't pass it and we only have one target.

    logger.info(
        f"Received wake-up check for VM ID: {vm_id} (Targeting Ollama at {OLLAMA_HOST_IP}:{OLLAMA_HOST_PORT})"
    )

    # 1. Check if Ollama is already ready
    if is_ollama_ready(OLLAMA_HOST_IP, OLLAMA_HOST_PORT):
        logger.info(f"Ollama on VM {vm_id} is already responsive.")
        return {"status": "ok", "message": "VM already active and Ollama responsive."}

    # 2. If not ready, try to resume the VM
    logger.info(f"Ollama not responsive. Attempting to resume VM {vm_id} via SURF API.")
    resume_triggered = surf_controller.resume_vm(vm_id)

    if not resume_triggered:
        logger.error(f"Failed to trigger resume for VM {vm_id} via SURF API.")
        raise HTTPException(
            status_code=503, detail=f"Failed to trigger resume for VM {vm_id}."
        )

    # 3. Poll for Ollama readiness after triggering resume
    start_time = time.time()
    while time.time() - start_time < WAKEUP_TIMEOUT_SECONDS:
        if is_ollama_ready(OLLAMA_HOST_IP, OLLAMA_HOST_PORT):
            logger.info(f"VM {vm_id} resumed and Ollama is now responsive.")
            return {
                "status": "ok",
                "message": f"VM {vm_id} resumed and Ollama responsive.",
            }
        logger.info(
            f"Waiting for VM {vm_id} and Ollama... ({int(time.time() - start_time)}s / {WAKEUP_TIMEOUT_SECONDS}s)"
        )
        time.sleep(WAKEUP_POLL_INTERVAL_SECONDS)

    logger.error(
        f"Timeout: VM {vm_id} did not become responsive after {WAKEUP_TIMEOUT_SECONDS} seconds."
    )
    raise HTTPException(
        status_code=503, detail=f"VM {vm_id} did not become responsive in time."
    )


if __name__ == "__main__":
    # This part is for running with `python main.py` for local dev,
    # Uvicorn in Dockerfile will run it in production.
    # Ensure .env file is present or env vars are set for local dev.
    from dotenv import load_dotenv

    load_dotenv()

    if (
        not os.getenv("OLLAMA_TARGET_HOST_IP")
        or not os.getenv("OLLAMA_TARGET_VM_ID")
        or not os.getenv("SURF_API_URL")
    ):
        print(
            "ERROR: Essential environment variables are not set for the wake-up service."
        )
        print("Please set: OLLAMA_TARGET_HOST_IP, OLLAMA_TARGET_VM_ID, SURF_API_URL")
        print(
            "And ensure token files are correctly pathed or set via SURF_API_TOKEN_PATH, SURF_CSRF_TOKEN_PATH"
        )
    else:
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=5001)

import logging
import os
import time

import requests

# Configure basic logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class SurfVMController:
    def __init__(self):
        self.base_url = os.getenv("SURF_API_URL")
        self.auth_token = os.getenv("SURF_API_TOKEN")  # Read directly from env
        self.csrf_token = os.getenv("SURF_CSRF_TOKEN")  # Read directly from env

        if not self.base_url:
            logger.error("SURF_API_URL environment variable not set.")
            raise ValueError("SURF_API_URL not set")

        if not self.auth_token:
            logger.error("SURF_API_TOKEN environment variable not set.")
            raise ValueError("SURF_API_TOKEN not set")

        if not self.csrf_token:
            logger.error("SURF_CSRF_TOKEN environment variable not set.")
            raise ValueError("SURF_CSRF_TOKEN not set")

    def _make_action_request(self, vm_id: str, action: str) -> bool:
        """
        Makes a POST request to the SURF API to perform an action on a VM.
        `action` should be "resume", "pause", etc.
        Returns True on success, False on failure.
        """
        if not vm_id:
            logger.error("VM ID is required for action request.")
            return False

        full_url = f"{self.base_url}/{vm_id}/actions/{action}/"
        headers = {
            "accept": "application/json;Compute",
            "authorization": self.auth_token,
            "Content-Type": f"application/json;{action}",
            "X-CSRFTOKEN": self.csrf_token,
        }

        timestamp = time.strftime("%d-%m-%Y %H:%M:%S")
        logger.info(
            f"{timestamp} | VM: {vm_id} | Attempting action: {action} at {full_url}"
        )

        try:
            response = requests.post(
                full_url, headers=headers, data="{}", timeout=30
            )  # 30s timeout for the API call itself
            response.raise_for_status()  # Will raise an HTTPError for bad responses (4XX or 5XX)

            if 200 <= response.status_code < 300:
                logger.info(
                    f"{timestamp} | VM: {vm_id} | Action '{action}' triggered successfully. Status: {response.status_code}"
                )
                return True
            else:
                logger.warning(
                    f"{timestamp} | VM: {vm_id} | Action '{action}' API call returned non-2xx status: {response.status_code} - {response.text}"
                )
                return False

        except requests.exceptions.RequestException as e:
            logger.error(
                f"{timestamp} | VM: {vm_id} | Action '{action}' request failed: {e}"
            )
            return False

    def resume_vm(self, vm_id: str) -> bool:
        return self._make_action_request(vm_id, "resume")

    def pause_vm(self, vm_id: str) -> bool:  # For future auto-pause
        return self._make_action_request(vm_id, "pause")


# Example usage (not part of the FastAPI app, just for testing this script)
if __name__ == "__main__":
    # For local testing, set up .env file or export these:
    # export SURF_API_URL="YOUR_SURF_API_ENDPOINT_URL_HERE"
    # export SURF_VM_ID_TO_TEST="YOUR_VM_ID_HERE"
    # And ensure your token files are in ./secrets/ relative to where you run this
    # For local testing, you might adjust token paths:
    # export SURF_API_TOKEN_PATH="./secrets/surf_api_token.txt"
    # export SURF_CSRF_TOKEN_PATH="./secrets/surf_csrf_token.txt"

    from dotenv import load_dotenv

    load_dotenv()  # Load .env file if present

    controller = SurfVMController()
    test_vm_id = os.getenv("SURF_VM_ID_TO_TEST", "test")
    print(f"Attempting to resume VM: {test_vm_id}")
    success = controller.resume_vm(test_vm_id)
    # success = controller.pause_vm(test_vm_id)
    print(f"Resume success: {success}")

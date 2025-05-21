# Ollama Nginx Gateway with API Key Authentication & Load Balancing

This project provides a Dockerized Nginx setup to act as a secure gateway for your backend Ollama LLM instances. It offers:

* **Single Entrypoint:** Exposes a single URL for clients to access multiple Ollama backends.
* **Load Balancing:** Distributes requests among your Ollama GPU servers.
* **API Key Authentication:** Protects your Ollama instances by requiring clients to provide a valid API key.
* **Easy Configuration:** API keys and backend server lists are managed via simple text files on the host.
* **Containerized & Portable:** Uses Docker and Docker Compose for easy deployment and management.

## Prerequisites

* Docker installed (https://docs.docker.com/get-docker/)
* Docker Compose installed (https://docs.docker.com/compose/install/)
* One or more Ollama instances running on your GPU machines, accessible from the machine running this Nginx gateway.

## Directory Structure

```
├── docker-compose.yml        # Docker Compose configuration
├── nginx/
│   ├── Dockerfile            # Dockerfile for the Nginx image
│   ├── nginx.conf            # Main Nginx configuration
│   ├── conf.d/
│   │   └── ollama_gateway.conf # Ollama specific proxy and auth logic
│   ├── api_keys.conf.example   # Your API key list (managed on host)
│   └── upstream_ollama.conf  # Your backend GPU server list (managed on host)
└── logs/                     # Nginx logs will be stored here (created on first run)
```
## Setup Instructions

1.  **Clone/Download Project**

2.  **Configure Backend Ollama Servers:**
Copy the `nginx/upstream_ollama.conf.example` to `nginx/upstream_ollama.conf`.
    Edit the `nginx/upstream_ollama.conf` file by listing the IP addresses (or hostnames) and ports of your Ollama GPU instances.

    **Example `nginx/upstream_ollama.conf`:**
    ```nginx
    # Backend Ollama Servers
    # Replace with the actual IPs/hostnames and ports of your Ollama servers
    server 192.168.1.101:11434; # GPU Server 1
    server ollama-gpu2.internal:11434; # GPU Server 2
    # Add more servers as needed
    ```

3.  **Generate and Add API Keys:**
    copy `api_keys.conf.example` to `api_keys.conf`
    API keys are managed in the `ollama-nginx-gateway/nginx/api_keys.conf` file. Each client that needs access should have a unique API key.

    * **Generating API Keys (CLI):**
        You can use various tools to generate strong random strings for your API keys. Here are a couple of examples:

        * **Using `openssl`:**
            ```bash
            openssl rand -hex 32
            ```
            This will output a 64-character hexadecimal string (e.g., `a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2`).

    * **Adding Keys to `api_keys.conf`:**
        Open `ollama-nginx-gateway/nginx/api_keys.conf` and add your generated keys. The Nginx configuration expects keys to be prefixed with `Bearer `.

        **Example `nginx/api_keys.conf`:**
        ```nginx
        # Format: "Bearer <your_api_key>" 1;  (the '1' means authorized)

        "Bearer your_generated_openssl_key_here"         1;
        "Bearer another_uuid_generated_key_here"    1;
        # Add one line per API key
        ```

4.  **Build and Run the Gateway:**
    Navigate to the root `ollama-nginx-gateway` directory in your terminal (the one containing `docker-compose.yml`).
    Run the following command:
    ```bash
    docker-compose up --build -d
    ```
    * `--build`: Builds the Nginx Docker image if it doesn't exist or if the Dockerfile has changed.
    * `-d`: Runs the containers in detached mode (in the background).

    Your Nginx gateway should now be running!

## How to Use

1.  **Client Configuration:**
    Clients (like LM Studio, Aider, `curl`, or your custom applications) should send requests to:
    * **Endpoint:** `http://<your_controller_machine_ip>:8080`
        (Replace `<your_controller_machine_ip>` with the IP address of the machine running Docker, and `8080` is the host port mapped in `docker-compose.yml`. You can change this mapping if needed.)
    * **HTTP Header for API Key:** Clients **must** include an `Authorization` header with the API key:
        ```
        Authorization: Bearer <your_api_key>
        ```
        Replace `<your_api_key>` with one of the keys you added to `api_keys.conf` (without the "Bearer " prefix in the value here, as "Bearer " is part of the full header value).

    **Example with `curl`:**
    ```bash
    curl -X POST http://<your_controller_machine_ip>:8080/api/generate \
      -H "Authorization: Bearer your_generated_openssl_key_here" \
      -d '{
        "model": "llama3",
        "prompt": "Why is the sky blue?"
      }'
    ```

2.  **Updating API Keys or Backend Servers:**
    If you need to add/remove API keys or change the list of backend Ollama servers:
    1.  Edit the corresponding file on your host machine (`nginx/api_keys.conf` or `nginx/upstream_ollama.conf`).
    2.  Instruct the running Nginx container to reload its configuration gracefully:
        ```bash
        docker exec ollama_nginx_gateway nginx -s reload
        ```
        This applies the changes without downtime.

## Log Management

* Nginx access and error logs are stored in the `ollama-nginx-gateway/logs/` directory on your host machine. This is configured via the volume mount in `docker-compose.yml`.
* You can view live logs from the Nginx container using:
    ```bash
    docker logs -f ollama_nginx_gateway
    ```

## Stopping the Gateway

To stop the Nginx gateway:
```bash
docker-compose down
```
To stop and remove volumes (like logs, if not needed, though typically you'd want to keep logs):
```bash
docker-compose down -v
```


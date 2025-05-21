# Use a modern Python slim image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.in .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.in

# Copy the rest of the application code
COPY . .

# Expose the port the app runs on
EXPOSE 5001

# Command to run the application using Uvicorn
# Uvicorn is a lightning-fast ASGI server, recommended for FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5001"]

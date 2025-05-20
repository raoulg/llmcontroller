# Use the official Nginx image from Docker Hub (Alpine version for smaller size)
FROM nginx:alpine

# Remove the default Nginx server configuration
RUN rm /etc/nginx/conf.d/default.conf

# Copy your main nginx.conf file to the appropriate location in the image
COPY nginx.conf /etc/nginx/nginx.conf

# Copy your Ollama gateway specific server block configuration
COPY conf.d/ollama_gateway.conf /etc/nginx/conf.d/ollama_gateway.conf

# Create a directory where custom mounted configurations like api_keys.conf will reside
# Nginx will look for api_keys.conf and upstream_ollama.conf in /etc/nginx/custom_conf/
# These files will be mounted from the host via docker-compose.
# We create placeholders here so Nginx doesn't complain if mounts are initially missing (though Docker Compose handles this well).
RUN mkdir -p /etc/nginx/custom_conf
RUN touch /etc/nginx/custom_conf/api_keys.conf
RUN touch /etc/nginx/custom_conf/upstream_ollama.conf

# Expose port 80 (which Nginx listens on inside the container)
EXPOSE 80

# Command to run Nginx in the foreground when the container starts
CMD ["nginx", "-g", "daemon off;"]

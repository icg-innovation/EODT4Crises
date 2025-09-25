# Use the official lightweight Nginx image from Docker Hub
FROM nginx:alpine

# Remove the default Nginx welcome page
RUN rm -rf /usr/share/nginx/html/*

# Copy the static content from the 'public' directory to the Nginx web root directory
COPY ./public/ /usr/share/nginx/html/

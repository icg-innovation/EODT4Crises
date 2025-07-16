# backend/Dockerfile

# 1. Use an official Python runtime as a parent image
FROM python:3.11-slim

# 2. Set the working directory inside the container
WORKDIR /app

# 3. Install system dependencies required by Python packages and gcloud
RUN apt-get update && apt-get install -y \
    libexpat1 \
    apt-transport-https \
    ca-certificates \
    gnupg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 4. Install the Google Cloud SDK
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | apt-key --keyring /usr/share/keyrings/cloud.google.gpg add - \
    && apt-get update && apt-get install -y google-cloud-sdk

# 5. Copy the requirements file into the container
COPY requirements.txt .

# 6. Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 7. Copy the rest of the backend application code into the container
COPY . .

# 8. Expose the port the app runs on
EXPOSE 4000

# 9. Define the command to run the application using Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:4000", "app:app"]


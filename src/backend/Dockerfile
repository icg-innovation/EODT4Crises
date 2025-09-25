# ---------------------------------------------------------
# GPU-enabled Dockerfile for your backend
# ---------------------------------------------------------

# Use NVIDIA's CUDA runtime image with Ubuntu 22.04
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu22.04

# Set a working directory
WORKDIR /app

# Install basic build tools and system libraries
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libspatialindex-dev \
    libgdal-dev \
    gdal-bin \
    libproj-dev \
    curl \
    ca-certificates \
    gnupg \
    git \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# Install Google Cloud SDK
RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends google-cloud-sdk && \
    rm -rf /var/lib/apt/lists/*

# Optional: Set GDAL paths if needed
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Install Python
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-venv \
        python3.11-dev \
    && ln -s /usr/bin/python3.11 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Download and install pip using the official bootstrap script
RUN curl https://bootstrap.pypa.io/get-pip.py | python3.11

# Copy requirements.txt
COPY requirements.txt .

# Install Python dependencies
RUN python3.11 -m pip install --no-cache-dir -r requirements.txt

# Copy your backend code
COPY . .

# Explicitly copy only case_studies into the image
COPY static/case_studies /app/static/case_studies

# Expose the backend port
EXPOSE 4000

# Run the backend via Gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:4000", "app:app"]
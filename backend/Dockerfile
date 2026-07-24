# Base image with CUDA support for A100. Production deploy requires this ARG to
# be overridden with a tag+sha256 digest from the known-good staging build.
ARG BACKEND_BASE_IMAGE=nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04
FROM ${BACKEND_BASE_IMAGE}

ARG WHISPERX_COMMIT=main

ENV DEBIAN_FRONTEND=noninteractive

# Set cache directories for models (will be persisted via volume)
ENV HF_HOME=/app/.cache/huggingface
ENV TORCH_HOME=/app/.cache/torch

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3.10-venv \
    git \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Create symlinks
RUN ln -sf /usr/bin/python3.10 /usr/bin/python && \
    ln -sf /usr/bin/pip3 /usr/bin/pip

# Upgrade pip
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Development may explicitly use "main". deploy.sh requires an immutable
# 40-character commit so production never resolves Git HEAD implicitly.
RUN pip install --no-cache-dir "whisperx @ git+https://github.com/m-bain/whisperx.git@${WHISPERX_COMMIT}"

# Create cache and working directories
RUN mkdir -p /app/.cache/huggingface /app/.cache/torch /app/audio /app/Doc

# Copy application files
COPY app/ /app/app/
COPY scripts/ /app/scripts/
COPY main.py .
COPY api.py .

# Default: run API server
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

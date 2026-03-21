FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Step 1: Install smaller/common dependencies first (cached separately)
RUN pip install --no-cache-dir --default-timeout=1000 \
    numpy \
    opencv-python-headless \
    paho-mqtt \
    pyproj \
    pydantic \
    typer \
    onnxruntime

# Step 2: Install heavy dependencies (Torch/Ultralytics) with increased timeout
RUN pip install --no-cache-dir --default-timeout=1000 \
    ultralytics

# Copy source code and config
COPY . /app/

# The video is already in /app/src/Video_Project_3.mp4 from the COPY step
WORKDIR /app/src

# Default command for cantina simulation
CMD ["python", "main.py", "--video", "Video_Project_3.mp4", "--camera-id", "CAM_CANTINA", "--direction", "right", "--mqtt-broker", "mosquitto", "--headless"]

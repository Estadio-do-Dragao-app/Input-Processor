FROM python:3.12-slim

# Install system dependencies (for OpenCV headless mode)
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libglvnd0 \
    libglx0 \
    libgl1 \
    libgomp1 \
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

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and config
COPY . /app/

# Ensure src directory exists and scripts are executable
WORKDIR /app
RUN chmod +x src/*.py 2>/dev/null || true

# Default command
CMD ["python3", "-u", "src/main.py"]

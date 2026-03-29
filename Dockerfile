FROM python:3.12-slim

# Disable Qt/GUI for headless OpenCV
ENV QT_QPA_PLATFORM=offscreen
ENV DISPLAY=

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

# Copy requirements FIRST (rarely changes)
COPY requirements.txt .

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

# Step 3: Install requirements.txt dependencies
RUN pip install --no-cache-dir -r requirements.txt

# NOW copy source code (this changes frequently, so it's last)
COPY . /app/

# Ensure scripts are executable
RUN chmod +x src/*.py 2>/dev/null || true

# Default command (camera-simulator/main.py; gps-processor uses docker-compose override)
CMD ["python3", "-u", "src/main.py"]

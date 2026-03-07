FROM python:3.11-slim

# Install system dependencies required for OpenCV and compilation
RUN apt-get update && apt-get install -y \
    libglib2.0-0 libsm6 libxext6 libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies (using opencv-python-headless for no-GUI server environments)
RUN pip install --no-cache-dir onnxruntime opencv-python-headless numpy typer paho-mqtt ultralytics

# Copy application files
COPY . /app

# Expose PYTHONPATH
ENV PYTHONPATH=/app/src

# Default execution command (overridden by docker-compose)
CMD ["python", "src/main.py", "--headless", "--mqtt-broker", "stadium-mosquitto", "--mqtt-port", "1883"]

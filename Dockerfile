# Autonomous Dockerfile for Input-Processor
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH=/app

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Cache ML Libraries explicitly
RUN pip install --no-cache-dir paho-mqtt opencv-python-headless numpy pydantic onnxruntime ultralytics

# Copy requirements
COPY requirements.txt* /app/
RUN if [ -f /app/requirements.txt ]; then pip install --no-cache-dir -r /app/requirements.txt; fi

# Copy service code (this invalidates cache only for changes below this line)
COPY . /app/

WORKDIR /app/src

# Default command
CMD ["python", "main.py", "--mqtt-broker", "mosquitto", "--headless"]

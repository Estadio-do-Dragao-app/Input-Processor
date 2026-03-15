# Autonomous Dockerfile for Input-Processor
FROM python:3.13-slim

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

# Cache essential libraries for GPS Processing
RUN pip install --no-cache-dir paho-mqtt pydantic pyproj

# Copy service code
COPY . /app/

WORKDIR /app/src

# Default command
CMD ["python", "main.py", "--mqtt-broker", "mosquitto", "--headless"]

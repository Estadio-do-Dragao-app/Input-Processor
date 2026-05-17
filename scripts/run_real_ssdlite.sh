#!/bin/bash

set -e

cd "$(dirname "$0")/.."

export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

PYTHON_BIN=""
if [ -f ".venv/bin/python3" ]; then
  PYTHON_BIN=".venv/bin/python3"
elif [ -f "../.venv/bin/python3" ]; then
  PYTHON_BIN="../.venv/bin/python3"
else
  PYTHON_BIN="python3"
  echo " Venv não encontrado, a usar python3 do sistema."
fi

MODEL_PATH_DEFAULT="../ssdlite_mobilenetv3small_pt_coco_person_300_qdq_int8.onnx-STM32MP257F-DK-code/ssdlite_mobilenetv3small_pt_coco_person_300_qdq_int8_OE_3_3_1.onnx"
MODEL_PATH="${SSDLITE_MODEL_PATH:-$MODEL_PATH_DEFAULT}"

CAMERA_ID="${1:-CAM_UA_001}"
ZONE_ID="${2:-0}"
CAMERA_INDEX="${3:-0}"
PUBLISH_INTERVAL="${4:-10}"

echo "════════════════════════════════════════════════════"
echo "Input-Processor | Real Camera + SSDLite (UA)"
echo "════════════════════════════════════════════════════"
echo "Camera ID:        ${CAMERA_ID}"
echo "Zone ID (level):  ${ZONE_ID}"
echo "Camera Index:     ${CAMERA_INDEX}"
echo "Publish Interval: ${PUBLISH_INTERVAL}s"
echo "Model:            ${MODEL_PATH}"
echo "MQTT Broker:      localhost:1883"
echo "════════════════════════════════════════════════════"

if [ ! -f "${MODEL_PATH}" ]; then
  echo " Modelo SSDLite não encontrado em: ${MODEL_PATH}"
  echo "   Define SSDLITE_MODEL_PATH com o caminho correto."
  exit 1
fi

"${PYTHON_BIN}" src/main.py \
  --mode ssdlite \
  --ssdlite-model-path "${MODEL_PATH}" \
  --mqtt-broker localhost \
  --mqtt-port 1883 \
  --camera-id "${CAMERA_ID}" \
  --level "${ZONE_ID}" \
  --camera-index "${CAMERA_INDEX}" \
  --publish-interval "${PUBLISH_INTERVAL}"

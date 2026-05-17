# Fan App

## Model
The main model we will use is a [ZIP](https://arxiv.org/pdf/2506.19955), you can find the official implementation in [here](https://github.com/Yiming-M/ZIP?tab=readme-ov-file). On the official implementation you can find the same model with different sizes but they arent quantized. However you can find one in the model directory (later on it will change, but the output shape should be the same)
If you need a datase I recommend [ShangaiTech](https://www.kaggle.com/datasets/tthien/shanghaitech), part A, there are other datasets, but they thend to be huge.

### Examples
The examples are similiar, but should give you an base idea on how you can use the model
```sh
uv run main.py # camera example
```
```sh
uv run image.py --help # image example
```

## Note
uv is a package manager for python, there are no problems if you install it globally on linux systems, but for safety you can use an venv
```sh
pip install uv; # you only need to do this once
uv sync; # install the packages
```

## Real camera with SSDLite (STM32 export ONNX)

To run a real USB camera with the `ssdlite_mobilenetv3small...` ONNX model:

```sh
cd Input-Processor
bash scripts/run_real_ssdlite.sh CAM_UA_001 0 0 10
```

Args are:
- `CAM_UA_001`: camera id sent in MQTT metadata
- `0`: zone id (`level` field is kept in events for compatibility)
- `0`: local camera index (`/dev/video0`; use `1` for `/dev/video1`, etc.)
- `10`: publish interval in seconds

If the model path is different, set:

```sh
export SSDLITE_MODEL_PATH="/absolute/or/relative/path/to/model.onnx"
```

import cv2
import numpy as np
import time
import onnxruntime as ort

class TemporalSmoother:
    """Suavização temporal usando Média Móvel Exponencial (EMA)"""
    def __init__(self, alpha=0.3):
        self.alpha = alpha
        self.smoothed_val = None

    def update(self, val):
        if self.smoothed_val is None:
            self.smoothed_val = float(val)
        else:
            self.smoothed_val = self.alpha * val + (1 - self.alpha) * self.smoothed_val
        return self.smoothed_val

class CrowdCounter:
    def __init__(self, mode="yolo", model_path="model/zip_n_model_quant.onnx",
                 yolo_model="yolov8n.pt", yolo_imgsz=1280, yolo_conf=0.15,
                 ssdlite_model_path=None, ssdlite_conf=0.55, ssdlite_nms_iou=0.45):
        """
        Initializes CrowdCounter in either 'yolo' or 'density' mode.

        YOLO-specific parameters:
            yolo_imgsz (int): Inference image size. Larger values improve small-person detection
                but increase CPU/GPU cost (default: 1280, tuned for accuracy).
            yolo_conf (float): Confidence threshold. Lower values detect more people (including
                smaller / harder cases) but can add noise and cost (default: 0.15).
        """
        self.mode = mode
        self.smoother = TemporalSmoother(alpha=0.4) # Alpha p/ equilibrar latência e estabilidade
        # Store YOLO inference settings so deployments can tune accuracy vs. performance
        self.yolo_imgsz = yolo_imgsz
        self.yolo_conf = yolo_conf
        self.ssdlite_conf = ssdlite_conf
        self.ssdlite_nms_iou = ssdlite_nms_iou
        print(f"🚀 Initializing CrowdCounter in [{self.mode.upper()}] mode...")
        
        if self.mode == "yolo":
            try:
                from ultralytics import YOLO
                self.model = YOLO(yolo_model)
                print(f"✅ YOLO Model Loaded: {yolo_model}")
            except Exception as e:
                raise RuntimeError(f"Failed to load YOLO model: {e}")
                
        elif self.mode == "density":
            try:
                self.session = ort.InferenceSession(model_path)
                self.input_name = self.session.get_inputs()[0].name
                # Model expects 256x256
                self.model_width = 256
                self.model_height = 256
                print(f"✅ Density Model Loaded: {model_path} ({self.model_width}x{self.model_height})")
            except Exception as e:
                raise RuntimeError(f"Failed to load ONNX model: {e}")
        elif self.mode == "ssdlite":
            try:
                if not ssdlite_model_path:
                    raise ValueError("SSDLite mode requires 'ssdlite_model_path'")

                self.ssdlite_session = ort.InferenceSession(ssdlite_model_path)
                self.ssdlite_input_name = self.ssdlite_session.get_inputs()[0].name
                self.ssdlite_out_names = [o.name for o in self.ssdlite_session.get_outputs()]
                self.ssdlite_model_width = 300
                self.ssdlite_model_height = 300

                # Quantization parameters exported with STM32 EdgeAI tooling.
                self.ssdlite_input_scale = 0.0078125
                self.ssdlite_input_zero = -1
                self.ssdlite_score_scale = 0.024268511682748795
                self.ssdlite_score_zero = -1
                self.ssdlite_box_scale = 0.09016282856464386
                self.ssdlite_box_zero = 74

                # SSD300 default boxes: 3000 priors = sum(fmap^2 * 6)
                self.ssdlite_priors = self._build_ssd300_priors()
                print(
                    f"✅ SSDLite Model Loaded: {ssdlite_model_path} "
                    f"({self.ssdlite_model_width}x{self.ssdlite_model_height}, priors={len(self.ssdlite_priors)})"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to load SSDLite model: {e}")
        else:
            raise ValueError(f"Unknown mode: {mode}")

    def preprocess_density(self, frame):
        """Prepare image for density model"""
        img = cv2.resize(frame, (self.model_width, self.model_height))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        
        # Standard ImageNet Normalization
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img = (img - mean) / std
        
        img = img.transpose(2, 0, 1)
        img = np.expand_dims(img, axis=0)
        return img

    def process_frame(self, frame):
        if self.mode == "yolo":
            return self._process_yolo(frame)
        elif self.mode == "density":
            return self._process_density(frame)
        elif self.mode == "ssdlite":
            return self._process_ssdlite(frame)
        return None, 0, None

    def _process_yolo(self, frame):
        """Fast inference using ONLY YOLO."""
        if frame is None: return None, 0, None
        
        # 1. Run YOLO with configurable resolution & confidence (defaults favor small-person detection)
        results = self.model.predict(
            frame,
            classes=[0],
            imgsz=self.yolo_imgsz,
            conf=self.yolo_conf,
            verbose=False,
        )
        
        # 2. Extract
        result = results[0]
        boxes = result.boxes.xyxy.cpu().numpy().astype(int)
        count = len(boxes)
        
        # 3. Synthetic Density Map
        h, w = frame.shape[:2]
        density_map = np.zeros((h, w), dtype=np.float32)
        
        for box in boxes:
            x1, y1, x2, y2 = box
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            sigma = max(5, (x2 - x1) // 6)
            radius = int(sigma * 2)
            cv2.circle(density_map, (cx, cy), radius, 1.0, -1)

        if count > 0:
            density_map = cv2.GaussianBlur(density_map, (31, 31), 0)
            cur_sum = np.sum(density_map)
            if cur_sum > 0:
                density_map = density_map * (count / cur_sum)
        
        # 4. Apply Smoothing
        count = self.smoother.update(count)
        
        return density_map, count, boxes

    def _process_density(self, frame):
        """Standard Density Model inference."""
        if frame is None: return None, 0, None

        # Preprocess
        blob = self.preprocess_density(frame)
        
        # Inference
        outputs = self.session.run(None, {self.input_name: blob})
        dmap = outputs[0][0][0]
        
        # Simple noise filter
        # 1. Thresholding (User reported best visual results with Morphological + 0.01)
        dmap[dmap < 0.01] = 0
        
        # 2. Morphological Opening to remove small noise speckles
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        dmap = cv2.morphologyEx(dmap, cv2.MORPH_OPEN, kernel)
        
        count = np.sum(dmap)
        
        # Resize to original frame size for consistency?
        # Actually main.py handles resizing for viz. 
        # But we should return the raw map.
        
        # 3. Apply Smoothing
        count = self.smoother.update(count)
        
        return dmap, count, None

    def preprocess_ssdlite(self, frame):
        """Prepare image for int8 SSDLite model ([1,3,300,300])."""
        img = cv2.resize(frame, (self.ssdlite_model_width, self.ssdlite_model_height))
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32)

        # Normalize to [-1, 1], then quantize according to model input scale/zero.
        img = (img / 127.5) - 1.0
        img_q = np.round((img / self.ssdlite_input_scale) + self.ssdlite_input_zero)
        img_q = np.clip(img_q, -128, 127).astype(np.int8)
        img_q = np.transpose(img_q, (2, 0, 1))
        img_q = np.expand_dims(img_q, axis=0)
        return img_q

    def _build_ssd300_priors(self):
        """Generate SSD300 priors (3000 anchors, 6 per spatial location)."""
        feature_maps = [19, 10, 5, 3, 2, 1]
        scales = [0.20, 0.34, 0.48, 0.62, 0.76, 0.90, 1.04]
        aspect_ratios = [2.0, 3.0]

        priors = []
        for k, fmap in enumerate(feature_maps):
            s_k = scales[k]
            s_next = scales[k + 1]
            for y in range(fmap):
                for x in range(fmap):
                    cx = (x + 0.5) / fmap
                    cy = (y + 0.5) / fmap

                    # 1) aspect ratio 1, scale s_k
                    priors.append([cx, cy, s_k, s_k])
                    # 2) aspect ratio 1, geometric mean scale
                    s_prime = float(np.sqrt(s_k * s_next))
                    priors.append([cx, cy, s_prime, s_prime])
                    # 3-6) aspect ratios 2 and 3
                    for ar in aspect_ratios:
                        ar_sqrt = float(np.sqrt(ar))
                        priors.append([cx, cy, s_k * ar_sqrt, s_k / ar_sqrt])
                        priors.append([cx, cy, s_k / ar_sqrt, s_k * ar_sqrt])

        return np.asarray(priors, dtype=np.float32)

    def _softmax_lastdim(self, x):
        x = x - np.max(x, axis=-1, keepdims=True)
        ex = np.exp(x)
        return ex / (np.sum(ex, axis=-1, keepdims=True) + 1e-8)

    def _nms_xyxy(self, boxes, scores, iou_threshold=0.45):
        if boxes.shape[0] == 0:
            return []

        x1 = boxes[:, 0]
        y1 = boxes[:, 1]
        x2 = boxes[:, 2]
        y2 = boxes[:, 3]
        areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        order = scores.argsort()[::-1]

        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)

            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-8)

            order = order[1:][iou < iou_threshold]

        return keep

    def _decode_ssd_boxes(self, loc):
        """
        Decode SSD box regressions with standard SSD weights (10,10,5,5).
        loc: [N,4], priors: [N,4] as (cx,cy,w,h), output xyxy normalized [0,1].
        """
        priors = self.ssdlite_priors
        if loc.shape[0] != priors.shape[0]:
            raise RuntimeError(
                f"SSDLite loc/prior size mismatch: got {loc.shape[0]} boxes, expected {priors.shape[0]}"
            )

        wx, wy, ww, wh = 10.0, 10.0, 5.0, 5.0
        dx = loc[:, 0] / wx
        dy = loc[:, 1] / wy
        dw = loc[:, 2] / ww
        dh = loc[:, 3] / wh

        cx = dx * priors[:, 2] + priors[:, 0]
        cy = dy * priors[:, 3] + priors[:, 1]
        w = np.exp(dw) * priors[:, 2]
        h = np.exp(dh) * priors[:, 3]

        x1 = np.clip(cx - (w / 2.0), 0.0, 1.0)
        y1 = np.clip(cy - (h / 2.0), 0.0, 1.0)
        x2 = np.clip(cx + (w / 2.0), 0.0, 1.0)
        y2 = np.clip(cy + (h / 2.0), 0.0, 1.0)

        return np.stack([x1, y1, x2, y2], axis=1)

    def _process_ssdlite(self, frame):
        if frame is None:
            return None, 0, None

        blob = self.preprocess_ssdlite(frame)
        outputs = self.ssdlite_session.run(None, {self.ssdlite_input_name: blob})

        # Identify outputs by shape to avoid hard dependency on output names.
        cls_raw = None
        box_raw = None
        for out in outputs:
            if out.ndim == 3 and out.shape[-1] == 2:
                cls_raw = out
            elif out.ndim == 3 and out.shape[-1] == 4:
                box_raw = out

        if cls_raw is None or box_raw is None:
            raise RuntimeError("SSDLite outputs not recognized (expected [N,3000,2] and [N,3000,4]).")

        # Dequantize outputs
        cls = (cls_raw.astype(np.float32) - self.ssdlite_score_zero) * self.ssdlite_score_scale
        loc = (box_raw.astype(np.float32) - self.ssdlite_box_zero) * self.ssdlite_box_scale

        # Convert logits -> probabilities (2 classes: background, person)
        probs = self._softmax_lastdim(cls)[0]
        person_scores = probs[:, 1]

        # Decode boxes to normalized xyxy
        decoded = self._decode_ssd_boxes(loc[0])
        keep_mask = person_scores >= self.ssdlite_conf

        if np.any(keep_mask):
            cand_boxes = decoded[keep_mask]
            cand_scores = person_scores[keep_mask]
            keep_idx = self._nms_xyxy(cand_boxes, cand_scores, iou_threshold=self.ssdlite_nms_iou)
            final_norm_boxes = cand_boxes[keep_idx]
        else:
            final_norm_boxes = np.empty((0, 4), dtype=np.float32)

        h, w = frame.shape[:2]
        if final_norm_boxes.shape[0] > 0:
            final_boxes = np.column_stack([
                (final_norm_boxes[:, 0] * w).astype(np.int32),
                (final_norm_boxes[:, 1] * h).astype(np.int32),
                (final_norm_boxes[:, 2] * w).astype(np.int32),
                (final_norm_boxes[:, 3] * h).astype(np.int32),
            ])
            # Remove degenerate boxes
            valid = (final_boxes[:, 2] > final_boxes[:, 0]) & (final_boxes[:, 3] > final_boxes[:, 1])
            final_boxes = final_boxes[valid]
        else:
            final_boxes = np.empty((0, 4), dtype=np.int32)

        count = float(final_boxes.shape[0])

        # Synthetic density map from person detections
        density_map = np.zeros((h, w), dtype=np.float32)
        for box in final_boxes:
            x1, y1, x2, y2 = box
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            sigma = max(5, (x2 - x1) // 6)
            radius = int(sigma * 2)
            cv2.circle(density_map, (cx, cy), radius, 1.0, -1)

        if count > 0:
            density_map = cv2.GaussianBlur(density_map, (31, 31), 0)
            cur_sum = np.sum(density_map)
            if cur_sum > 0:
                density_map = density_map * (count / cur_sum)

        count = self.smoother.update(count)
        return density_map, count, final_boxes

import cv2
import numpy as np
import argparse
import time
import sys
import os
from pathlib import Path
import threading
import paho.mqtt.client as mqtt

# Importar módulos locais
try:
    from crowd_counter import CrowdCounter
    CROWD_COUNTER_AVAILABLE = True
except ImportError as e:
    print(f"❌ Erro ao importar crowd_counter: {e}")
    sys.exit(1)

# Importar MQTT publisher
try:
    from camera_mqtt_publisher import CameraMQTTPublisher
    MQTT_AVAILABLE = True
except ImportError:
    print("⚠️  camera_mqtt_publisher não disponível")
    MQTT_AVAILABLE = False


def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Fan App - Real-time Crowd Counting')
    # Read MQTT broker from environment variable or use command-line arg
    mqtt_broker_env = os.getenv('MQTT_BROKER_HOST', 'localhost')
    mqtt_port_env = int(os.getenv('MQTT_BROKER_PORT', '1883'))
    parser.add_argument('--mqtt-broker', default=mqtt_broker_env, help='MQTT broker host')
    parser.add_argument('--mqtt-port', type=int, default=mqtt_port_env, help='MQTT broker port')
    parser.add_argument('--camera-id', default='CAM_001', help='Camera identifier')
    parser.add_argument('--level', type=int, default=0, choices=[0, 1], help='Stadium level')
    parser.add_argument('--publish-interval', type=int, default=10, help='MQTT publish interval (seconds)')
    parser.add_argument('--no-mqtt', action='store_true', help='Disable MQTT publishing')
    parser.add_argument('--mode', type=str, default='yolo', choices=['yolo', 'density'], help='Detection mode: yolo or density')
    parser.add_argument('--headless', action='store_true', help='Disable OpenCV GUI window')
    parser.add_argument('--subscribe-topic', type=str, default='', help='If provided, listen to this MQTT topic for JPEG camera frames instead of USB.')
    parser.add_argument('--video', type=str, default=None, help='Path to input video file')
    parser.add_argument('--output-video', type=str, default=None, help='Path to save overlay video')
    parser.add_argument('--spacing', type=int, default=30, help='Estimated pixels per person in queue')
    parser.add_argument('--service-rate', type=float, default=1.0, help='Default service rate (people/min)')
    parser.add_argument('--direction', type=str, default='right', choices=['right', 'left', 'up', 'down'], help='Queue movement direction')
    parser.add_argument('--threshold', type=float, default=0.1, help='Movement threshold in pixels per frame')
    parser.add_argument('--min-fraction', type=float, default=0.04, help='Minimum fraction of pixels that must be moving')
    args = parser.parse_args()
    
    # Print configuration
    print("=" * 60)
    print("🎥 FAN APP - Real-time Crowd Counting (Optimized Architecture)")
    print("=" * 60)
    print(f"Camera ID: {args.camera_id}")
    print(f"Mode: {args.mode.upper()}")
    print(f"Level: {args.level}")
    
    if not args.no_mqtt and MQTT_AVAILABLE:
        print(f"MQTT Broker: {args.mqtt_broker}:{args.mqtt_port}")
        print(f"Publish Interval: {args.publish_interval}s")
        mqtt_enabled = True
    else:
        print("MQTT: Disabled")
        mqtt_enabled = False
    print("=" * 60)
    print()
    
    # 1. Initialize Crowd Counter (Loads model ONCE)
    model_path = "model/zip_n_model_quant.onnx"
    try:
        counter = CrowdCounter(mode=args.mode, model_path=model_path)
    except Exception as e:
        print(f"❌ Falha crítica ao iniciar CrowdCounter: {e}")
        return 1
    
    print()
    
    # 2. Initialize MQTT publisher if enabled
    publisher = None
    if mqtt_enabled:
        try:
            publisher = CameraMQTTPublisher(
                camera_id=args.camera_id,
                level=args.level,
                mqtt_broker=args.mqtt_broker,
                mqtt_port=args.mqtt_port
            )
            print()
        except Exception as e:
            print(f"⚠️  Falha ao inicializar MQTT: {e}")
            print("   Continuando sem MQTT...")
            publisher = None

    # 2.5 Start MQTT Camera Receiver (Optional)
    latest_mqtt_frame = None
    mqtt_frame_lock = threading.Lock()
    camera_mqtt_client = None

    if args.subscribe_topic:
        def on_camera_message(client, userdata, msg):
            nonlocal latest_mqtt_frame
            try:
                np_arr = np.frombuffer(msg.payload, np.uint8)
                decoded = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                if decoded is not None:
                    with mqtt_frame_lock:
                        latest_mqtt_frame = decoded
            except Exception as e:
                pass # Ignore malformed frames silently to prevent log spam
                
        print(f"📡 Configurando receção de vídeo MQTT no tópico: {args.subscribe_topic}")
        import uuid
        camera_mqtt_client = mqtt.Client(client_id=f"cam_listener_{uuid.uuid4().hex[:6]}")
        camera_mqtt_client.on_message = on_camera_message
        
        try:
            camera_mqtt_client.connect(args.mqtt_broker, args.mqtt_port, 60)
            camera_mqtt_client.subscribe(args.subscribe_topic, qos=0)
            camera_mqtt_client.loop_start()
            print("✅ Ligado ao stream de vídeo MQTT com sucesso!")
        except Exception as e:
            print(f"❌ Erro ao ligar ao stream MQTT: {e}")
            camera_mqtt_client = None

    # 3. Start Camera (Try Video file, then MQTT, then physical webcam, then mock)
    cap = None
    is_mock = False
    mock_image_path = None
    
    if args.video:
        cap = cv2.VideoCapture(args.video)
        if not cap.isOpened():
            print(f"❌ Não foi possivel abrir o vídeo: {args.video}")
            return 1
        print(f"▶️ Vídeo iniciado: {args.video}")
    elif camera_mqtt_client is not None:
        print(f"▶️ À espera de imagens MQTT em: {args.subscribe_topic}")
    else:
        # Try local camera
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("⚠️ Não foi possivel abrir a webcam física (/dev/video0).")
            print("🔧 Iniciando modo SIMULAÇÃO com imagem de teste (yolo_1280.jpg)...")
            mock_image_path = Path(__file__).parent.parent / "yolo_1280.jpg"
            if not mock_image_path.exists():
                print(f"❌ Imagem de teste {mock_image_path} não encontrada. Abortando.")
                return 1
            is_mock = True
        else:
            print("▶️ Câmera física iniciada.")
    
    # Optional Video Writer for saving the output
    video_writer = None
    if args.output_video:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        # We'll initialize it when the first frame arrives to get the size
        
    print()
    
    last_publish_time = 0
    frame_count = 0
    prev_gray = None
    
    # For smoothing directional speed
    smoothed_directional_speed = None
    smoothing_alpha = 0.05
    max_wait_time = 3600
    
    # For smoothing final wait time (MM:SS stability)
    smoothed_wait_time_sec = None
    wait_time_smoothing_alpha = 0.1
    
    while True:
        if camera_mqtt_client:
            with mqtt_frame_lock:
                frame = latest_mqtt_frame.copy() if latest_mqtt_frame is not None else None
            if frame is None:
                time.sleep(0.1)
                continue
        elif is_mock:
            frame = cv2.imread(str(mock_image_path))
            if frame is None:
                print("❌ Falha crítica ao ler a imagem de teste.")
                break
            time.sleep(1) # Simulate 1 fps in mock mode to avoid CPU spin
        else:
            ret, frame = cap.read()
            if not ret: 
                if args.video:
                    print("🔄 Repeating video...")
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                    if not ret:
                        break
                else:
                    print("⚠️ Falha ao ler frame da webcam.")
                    break

        # 4. SINGLE INFERENCE POINT
        # Process frame creates density map and count
        res = counter.process_frame(frame)
        if len(res) == 3:
            density_map, count, boxes = res
        else:
            density_map, count = res
            boxes = None

        if density_map is None:
            print("⚠️ Falha no processamento do frame")
            continue
            
        # --- WAIT TIME CALCULATION (Optical Flow) ---
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        directional_speed = 0.0
        moving_fraction = 0.0
        
        if prev_gray is not None:
            # Performance Optimization: Downsample for flow
            flow_w, flow_h = 448, 448
            frame_h, frame_w = gray.shape[:2]
            gray_small = cv2.resize(gray, (flow_w, flow_h))
            prev_gray_small = cv2.resize(prev_gray, (flow_w, flow_h))
            
            flow = cv2.calcOpticalFlowFarneback(prev_gray_small, gray_small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            
            # Spatial filter with density mask
            density_mask = cv2.resize(density_map, (flow_w, flow_h))
            density_mask = density_mask > 0.05
            
            # Directional filtering
            if args.direction == 'right':
                flow_vec = flow[..., 0]
                directional_pixels = flow_vec[(flow_vec > args.threshold) & density_mask]
            elif args.direction == 'left':
                flow_vec = flow[..., 0]
                directional_pixels = -flow_vec[(flow_vec < -args.threshold) & density_mask]
            elif args.direction == 'down':
                flow_vec = flow[..., 1]
                directional_pixels = flow_vec[(flow_vec > args.threshold) & density_mask]
            elif args.direction == 'up':
                flow_vec = flow[..., 1]
                directional_pixels = -flow_vec[(flow_vec < -args.threshold) & density_mask]
            
            # Calculate metrics
            pp_count = np.sum(density_mask)
            moving_fraction = directional_pixels.size / pp_count if pp_count > 10 else 0.0
            
            if directional_pixels.size > 0:
                directional_speed = float(np.median(directional_pixels))
                directional_speed *= (frame_w / flow_w) # Rescale
                
        # Update Smoothing
        if smoothed_directional_speed is None:
            smoothed_directional_speed = directional_speed
        else:
            smoothed_directional_speed = (smoothing_alpha * directional_speed + 
                                          (1 - smoothing_alpha) * smoothed_directional_speed)
        
        prev_gray = gray
        
        # Blended Rate Logic
        fps = 25 # Default for stream
        if not is_mock and cap:
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            
        measured_rate = (smoothed_directional_speed * fps) / args.spacing if smoothed_directional_speed > 0 else 0.0
        default_rate = args.service_rate / 60.0
        confidence = min(1.0, moving_fraction / 0.05)
        
        blended_rate = (confidence * measured_rate) + ((1 - confidence) * default_rate)
        instant_wait = count / blended_rate if blended_rate > 1e-4 else max_wait_time
        
        # Double Smoothing
        if smoothed_wait_time_sec is None:
            smoothed_wait_time_sec = instant_wait
        else:
            smoothed_wait_time_sec = (wait_time_smoothing_alpha * instant_wait + 
                                      (1 - wait_time_smoothing_alpha) * smoothed_wait_time_sec)
        
        final_wait = min(smoothed_wait_time_sec, max_wait_time)
        wait_time_str = f"{int(final_wait // 60):02d}:{int(final_wait % 60):02d}"
        
        # Status
        if moving_fraction > args.min_fraction:
            status_str, status_color = "MOVING", (0, 255, 0)
        else:
            status_str, status_color = "STAGNANT", (0, 165, 255)
        
        # 5. MQTT Publishing (Reuse results!)
        current_time = time.time()
        if publisher and (current_time - last_publish_time) >= args.publish_interval:
            try:
                # Passamos os resultados JÁ CALCULADOS para o publisher
                publisher.publish_event_data(density_map, count, boxes=boxes, grid_resolution=10, wait_time_sec=final_wait)
                last_publish_time = current_time
            except Exception as e:
                print(f"❌ Erro ao publicar: {e}")

        # 6. Generate Heatmap Visualization (Only for Density mode)
        if args.mode == "density":
            # Resize density map to match original frame size for visualization
            density_resized = cv2.resize(density_map, (frame.shape[1], frame.shape[0]))
            
            # Normalize density map for visualization
            if density_resized.max() > density_resized.min():
                vis_map = (density_resized - density_resized.min()) / (density_resized.max() - density_resized.min())
            else:
                vis_map = np.zeros_like(density_resized)
            
            vis_map = (vis_map * 255).astype(np.uint8)
            heatmap = cv2.applyColorMap(vis_map, cv2.COLORMAP_JET)
            
            # 7. Overlay Heatmap
            result = cv2.addWeighted(frame, 0.6, heatmap, 0.4, 0)
        else:
            # YOLO Mode: Use raw frame and draw rectangles (no "balls")
            result = frame.copy()
            
            # 7.2 Draw Bounding Boxes (Rectangles)
            if boxes is not None:
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box)
                    cv2.rectangle(result, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # 7.5 Save to output video if requested
        if args.output_video:
            if video_writer is None:
                h, w = result.shape[:2]
                fps = 25
                if cap:
                    fps = cap.get(cv2.CAP_PROP_FPS) or 25
                video_writer = cv2.VideoWriter(args.output_video, fourcc, fps, (w, h))
            video_writer.write(result)

        # 8. Draw UI
        if not args.headless:
            # Background for text
            cv2.rectangle(result, (0, 0), (400, 160), (0, 0, 0), -1)
            
            # Main Info
            cv2.putText(result, f"People: {int(count)}", (10, 35), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.putText(result, f"Wait Time: {wait_time_str}", (10, 75), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 0), 2)
            
            # Diagnostic Info
            cv2.putText(result, f"Status: {status_str}", (10, 115), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 1)
            
            camera_name = args.camera_id
            mqtt_info = "Active" if (publisher and publisher.mqtt_connected) else "Offline"
            cv2.putText(result, f"Camera: {camera_name} | MQTT: {mqtt_info}", (10, 145), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            cv2.imshow('EBC Crowd Counting - Real-time', result)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        else:
            # Em modo headless, imprimir log básico ou apenas continuar processando
            pass

        frame_count += 1

    # Release camera resource if it was initialized
    if cap is not None and hasattr(cap, "isOpened") and cap.isOpened():
        cap.release()

    if not args.headless:
        cv2.destroyAllWindows()
    
    # Release video writer
    if video_writer:
        video_writer.release()
        print(f"💾 Vídeo salvo em: {args.output_video}")
    
    # Disconnect MQTT publishers
    if publisher:
        publisher.disconnect()

    # Disconnect MQTT camera client (if used)
    if camera_mqtt_client is not None:
        try:
            camera_mqtt_client.loop_stop()
        except Exception:
            pass
        try:
            camera_mqtt_client.disconnect()
        except Exception:
            pass
    
    print("\n✅ Aplicação encerrada")
    return 0

if __name__ == "__main__":
    sys.exit(main())
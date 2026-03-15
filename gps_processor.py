import json
import time
import os
import uuid
from datetime import datetime
import threading
from pyproj import Proj, Transformer
import paho.mqtt.client as mqtt
from schemas import CrowdDensityEvent, GridCell, QueueEvent

# Configurações MQTT
MQTT_BROKER = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
GPS_TOPIC = "stadium/location/gps"
CONGESTION_TOPIC = "stadium/events/congestion"
QUEUE_TOPIC = "stadium/events/queues"

# Estado
# user_id -> {"lat": float, "lng": float, "timestamp": float}
active_users = {}
USER_TTL_SECONDS = 30
GRID_RESOLUTION = 10  # Metros
UPDATE_INTERVAL = 10  # Segundos

# Helper para converter Lat/Lng WGS84 para coordenadas cartesianas locais (EPSG:3763 - PT-TM06)
# O Map-Service costuma usar uma projeção do género, mas vamos usar um transformer simples para X/Y em metros
# Se o Dragão usar Coordenadas Locais relativas a um ponto base:
BASE_LAT = 41.1617
BASE_LNG = -8.5836

def latlng_to_meters(lat, lng):
    # Muito aproximado: 1 grau de lat ~ 111km, 1 grau lng ~ 111km * cos(lat)
    dy = (lat - BASE_LAT) * 111000
    dx = (lng - BASE_LNG) * 111000 * 0.752  # cos(41.1617) aprox
    return dx, dy

# ROIs (Filas)
rois = []
roi_path = os.getenv("ROIS_PATH", "rois.json")
try:
    if os.path.exists(roi_path):
        with open(roi_path, 'r') as f:
            all_rois = json.load(f)
            # Para o GPS não temos "camera_id", vamos carregar TODAS as ROIs num só nível
            for cam, cam_rois in all_rois.items():
                rois.extend(cam_rois)
        print(f"✅ GPS Processor: {len(rois)} ROIs globais carregadas.")
except Exception as e:
    print(f"⚠️  GPS Processor: Erro a ler ROIs: {e}")

def point_in_polygon(x, y, poly):
    # Baseado em cv2.pointPolygonTest, mas puro Python (ray casting alg)
    n = len(poly)
    inside = False
    p1x, p1y = poly[0]
    for i in range(n+1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y-p1y)*(p2x-p1x)/(p2y-p1y)+p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def on_connect(client, userdata, flags, rc, *args):
    if rc == 0:
        print(f"✅ GPS Processor: Conectado ao MQTT {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(GPS_TOPIC)
        print(f"📡 A escutar: {GPS_TOPIC}")
    else:
        print(f"❌ GPS Processor: Falha ao conectar (código {rc})")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode('utf-8'))
        user_id = payload.get("user_id")
        lat = payload.get("lat")
        lng = payload.get("lng")
        
        if user_id and lat is not None and lng is not None:
            active_users[user_id] = {
                "lat": float(lat),
                "lng": float(lng),
                "timestamp": time.time()
            }
    except Exception as e:
        pass # Ignorar mensagens mal formatadas silenciosamente

def aggregate_and_publish(client):
    while True:
        time.sleep(UPDATE_INTERVAL)
        current_time = time.time()
        
        # 1. Limpar stale users
        stale = [uid for uid, data in active_users.items() if (current_time - data["timestamp"]) > USER_TTL_SECONDS]
        for uid in stale:
            active_users.pop(uid, None)
            
        if not active_users:
            continue
            
        print(f"👥 GPS Processor: Agregando {len(active_users)} utilizadores ativos...")
        
        # 2. Mapeamento para Congestion (Grid)
        grid_counts = {}
        points_xy = []
        
        for data in active_users.values():
            x, y = latlng_to_meters(data["lat"], data["lng"])
            points_xy.append((x, y))
            
            # Snap to grid cell (Centro da célula)
            grid_x = (int(x // GRID_RESOLUTION) * GRID_RESOLUTION) + (GRID_RESOLUTION / 2)
            grid_y = (int(y // GRID_RESOLUTION) * GRID_RESOLUTION) + (GRID_RESOLUTION / 2)
            cell_key = (grid_x, grid_y)
            
            grid_counts[cell_key] = grid_counts.get(cell_key, 0) + 1
            
        # Converter para formato de schema unificado
        grid_data = [
            {"x": gx, "y": gy, "count": count}
            for (gx, gy), count in grid_counts.items()
        ]
        
        # Level 0 (Piso principal) assumido nesta versão inicial baseada unicamente em GPS
        # Pode ser melhorado para receber piso/altimetria do telemóvel
        event = CrowdDensityEvent.create(
            level=0, 
            grid_data=grid_data, 
            camera_id="GPS_AGGREGATOR"
        )
        
        client.publish(CONGESTION_TOPIC, event.model_dump_json(), qos=0)
        
        # 3. Mapeamento para Queues (ROIs)
        if rois:
            for roi in rois:
                poly = roi.get("polygon", []) # Poligonos devem ser ajustados para metros ou lat/lng
                if not poly: continue
                
                count = 0
                for px, py in points_xy:
                    # Assumimos que o ROIS JSON foi definido como X/Y no referencial em metros correspondente, 
                    # OU em Lat/Lng puro. O utilizador terá de definir isso. 
                    if point_in_polygon(px, py, poly):
                        count += 1
                
                if count > 0:
                    q_event = QueueEvent.create(
                        location_type=roi["type"],
                        location_id=roi["id"],
                        queue_length=count,
                        camera_id="GPS_AGGREGATOR"
                    )
                    client.publish(QUEUE_TOPIC, q_event.model_dump_json(), qos=0)

if __name__ == "__main__":
    print("🚀 A Iniciar GPS Processor...")
    
    # Suporte para paho-mqtt 2.0+
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gps_aggregator_service")
    except AttributeError:
        client = mqtt.Client(client_id="gps_aggregator_service")
        
    client.on_connect = on_connect
    client.on_message = on_message
    
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    
    # Thread para loop de mensagens
    client.loop_start()
    
    # Thread (Main) para publicações periódicas
    try:
        aggregate_and_publish(client)
    except KeyboardInterrupt:
        print("Terminando...")
        client.loop_stop()
        client.disconnect()

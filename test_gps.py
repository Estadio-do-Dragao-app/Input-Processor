import paho.mqtt.client as mqtt
import json
import time
import random
import uuid
import os
import ssl
import math
from datetime import datetime, timezone

# Configuration
MQTT_BROKER = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "8883"))
TOPIC = "stadium/location/gps"
CONGESTION_TOPIC = "stadium/events/congestion"
QUEUE_TOPIC = "stadium/events/queues"
MQTT_USER = os.getenv("MQTT_USER", "services")
MQTT_PASS = os.getenv("MQTT_PASS", "dragao_mqtt_2026")
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "/certs/ca.crt")
NUM_USERS = int(os.getenv("GPS_SIM_NUM_USERS", "1500"))
ACTIVE_PER_TICK = int(os.getenv("GPS_SIM_ACTIVE_USERS", "900"))
UPDATE_INTERVAL = float(os.getenv("GPS_SIM_UPDATE_INTERVAL", "8"))
GRID_RESOLUTION = 10
GPS_CAMERA_ID = "GPS_AGGREGATOR"
VISUALIZATION_TOPIC = os.getenv("GPS_SIM_VIS_TOPIC", "stadium/visualization/people")

# Offset no referencial do estádio (mesma origem que o stadiumCenter no Fanapp).
# Deixa em 0 se este mock for a única fonte; usa valores não-zero para evitar
# colisão de coordenadas com outras câmaras (ex.: CAM_001 com offset (-40,-30)).
STADIUM_OFFSET_X = float(os.getenv("GPS_SIM_OFFSET_X", "0"))
STADIUM_OFFSET_Y = float(os.getenv("GPS_SIM_OFFSET_Y", "0"))

# Create a larger set of fake user IDs
USER_IDS = [str(uuid.uuid4()) for _ in range(NUM_USERS)]

# Dragão base coordinates - Expanded to UA Campus area
BASE_LAT = 40.6300
BASE_LNG = -8.6558


def meters_to_latlng(dx, dy):
    lat = BASE_LAT + (dy / 111000)
    lng = BASE_LNG + (dx / (111000 * 0.752))
    return lat, lng


WALKER_ROUTES = [
    # Norte (DETI, engenharia)
    [(-200, 350), (100, 350), (100, 450), (-200, 450)],
    # Sul (Crasto, residências)
    [(-150, -400), (200, -400), (200, -250), (-150, -250)],
    # Oeste (entrada principal, biblioteca)
    [(-330, -100), (-200, -100), (-200, 100), (-330, 100)],
    # Este (DECA, ISCA)
    [(300, -150), (450, -150), (450, 200), (300, 200)],
    # Centro do campus (reitoria)
    [(-50, 0), (150, 0), (150, 200), (-50, 200)],
    # Próximo da origem
    [(-100, -100), (50, -100), (50, 50), (-100, 50)],
]



def build_walker(uid, idx):
    route = WALKER_ROUTES[idx % len(WALKER_ROUTES)]
    start_x, start_y = route[idx % len(route)]
    next_waypoint = (idx + 1) % len(route)
    lat, lng = meters_to_latlng(start_x, start_y)
    return {
        "user_id": uid,
        "route": route,
        "route_index": idx % len(route),
        "x": float(start_x),
        "y": float(start_y),
        "lat": float(lat),
        "lng": float(lng),
        "speed_mps": 5.0 + (idx % 4) * 1.5,
        "target_index": next_waypoint,
    }


WALKERS = [build_walker(uid, i) for i, uid in enumerate(USER_IDS)]

ROIS = []
roi_path = os.getenv("ROIS_PATH", "rois.json")
try:
    if os.path.exists(roi_path):
        with open(roi_path, "r") as handle:
            all_rois = json.load(handle)
            for roi_group in all_rois.values():
                ROIS.extend(roi_group)
except Exception:
    ROIS = []


def create_client():
    client = mqtt.Client(client_id=f"gps_mock_{uuid.uuid4().hex[:6]}")
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    if MQTT_CA_CERT and os.path.exists(MQTT_CA_CERT):
        client.tls_set(ca_certs=MQTT_CA_CERT, tls_version=ssl.PROTOCOL_TLS_CLIENT)
        client.tls_insecure_set(False)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client


def advance_walker(walker, delta_seconds):
    route = walker["route"]
    target = route[walker["target_index"]]
    dx = target[0] - walker["x"]
    dy = target[1] - walker["y"]
    distance = math.hypot(dx, dy)

    if distance < 0.5:
        walker["route_index"] = walker["target_index"]
        walker["target_index"] = (walker["target_index"] + 1) % len(route)
        target = route[walker["target_index"]]
        dx = target[0] - walker["x"]
        dy = target[1] - walker["y"]
        distance = math.hypot(dx, dy)

    step = walker["speed_mps"] * delta_seconds
    if distance > 0:
        ratio = min(step / distance, 1.0)
        walker["x"] += dx * ratio
        walker["y"] += dy * ratio

    walker["lat"], walker["lng"] = meters_to_latlng(walker["x"], walker["y"])


def point_in_polygon(x, y, poly):
    inside = False
    p1x, p1y = poly[0]
    for i in range(len(poly) + 1):
        p2x, p2y = poly[i % len(poly)]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def build_crowd_density_event(active_walkers):
    grid_counts = {}
    points_xy = []

    for walker in active_walkers:
        points_xy.append((walker["x"], walker["y"]))
        grid_x = (int(walker["x"] // GRID_RESOLUTION) * GRID_RESOLUTION) + (GRID_RESOLUTION / 2) + STADIUM_OFFSET_X
        grid_y = (int(walker["y"] // GRID_RESOLUTION) * GRID_RESOLUTION) + (GRID_RESOLUTION / 2) + STADIUM_OFFSET_Y
        cell_key = (grid_x, grid_y)
        grid_counts[cell_key] = grid_counts.get(cell_key, 0) + 1

    grid_data = [
        {"x": gx, "y": gy, "count": count}
        for (gx, gy), count in grid_counts.items()
    ]

    return {
        "event_id": f"gps-sim-{uuid.uuid4()}",
        "event_type": "crowd_density",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": 0,
        "grid_data": grid_data,
        "total_people": len(active_walkers),
        "metadata": {
            "camera_id": GPS_CAMERA_ID,
            "coordinate_unit": "meters",
            "source": "gps-simulator"
        }
    }, points_xy


def publish_queue_events(client, points_xy):
    for roi in ROIS:
        poly = roi.get("polygon", [])
        if not poly:
            continue

        count = 0
        for px, py in points_xy:
            if point_in_polygon(px, py, poly):
                count += 1

        if count > 0:
            payload = {
                "event_id": f"gps-queue-{roi['id']}-{uuid.uuid4()}",
                "event_type": "queue_update",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "location_type": roi.get("type", "queue"),
                "location_id": roi["id"],
                "queue_length": count,
                "metadata": {
                    "camera_id": GPS_CAMERA_ID,
                    "source": "gps-simulator"
                }
            }
            client.publish(QUEUE_TOPIC, json.dumps(payload), qos=0)


def main():
    client = create_client()
    print(f"Conectado mock a {MQTT_BROKER}:{MQTT_PORT} no tópico {TOPIC}")
    print(f"Publicando {NUM_USERS} utilizadores simulados, com {ACTIVE_PER_TICK} ativos por ciclo")

    try:
        last_tick = time.time()
        while True:
            now = time.time()
            delta_seconds = max(now - last_tick, 0.1)

            active_this_round = random.sample(WALKERS, min(ACTIVE_PER_TICK, len(WALKERS)))

            for walker in active_this_round:
                advance_walker(walker, delta_seconds)

                client.publish(
                    TOPIC,
                    json.dumps({
                        "user_id": walker["user_id"],
                        "lat": walker["lat"],
                        "lng": walker["lng"],
                        "timestamp": time.time(),
                    }),
                    qos=0,
                )

            viz_payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "gps-simulator",
                "use_camera": False,
                "users": [
                    {"user_id": w["user_id"], "lat": w["lat"], "lng": w["lng"]}
                    for w in active_this_round
                ],
            }
            client.publish(VISUALIZATION_TOPIC, json.dumps(viz_payload), qos=0)

            crowd_event, points_xy = build_crowd_density_event(active_this_round)
            client.publish(CONGESTION_TOPIC, json.dumps(crowd_event), qos=0)
            publish_queue_events(client, points_xy)

            print(f"Publicados {len(active_this_round)} eventos GPS falsos com caminhada simulada.")

            last_tick = time.time()
            time.sleep(UPDATE_INTERVAL)

    except KeyboardInterrupt:
        print("\nSaindo...")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

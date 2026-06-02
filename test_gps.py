"""
GPS Simulator — Realistic University of Aveiro Campus Simulation
================================================================
Generates synthetic pedestrian movement across the UA campus.
Users walk between real POIs (departments, canteen, library, etc.)
with organic movement (Gaussian jitter, variable speed, random pauses).

Publishes:
  - stadium/location/gps           — individual GPS updates
  - stadium/events/congestion      — aggregated crowd density grid (Gaussian-smoothed)
  - stadium/events/queues          — queue events for ROI regions
  - stadium/visualization/people   — per-user positions for frontend
"""

import paho.mqtt.client as mqtt
import json
import time
import random
import uuid
import os
import ssl
import math
from datetime import datetime, timezone

# ============================================================
# Configuration
# ============================================================
MQTT_BROKER = os.getenv("MQTT_BROKER_HOST", "mosquitto")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", "8883"))
TOPIC = "stadium/location/gps"
CONGESTION_TOPIC = "stadium/events/congestion"
QUEUE_TOPIC = "stadium/events/queues"
MQTT_USER = os.getenv("MQTT_USER", "services")
MQTT_PASS = os.getenv("MQTT_PASS", "dragao_mqtt_2026")
MQTT_CA_CERT = os.getenv("MQTT_CA_CERT", "/certs/ca.crt")
NUM_USERS = int(os.getenv("GPS_SIM_NUM_USERS", "100"))
ACTIVE_PER_TICK = int(os.getenv("GPS_SIM_ACTIVE_USERS", "100"))
UPDATE_INTERVAL = float(os.getenv("GPS_SIM_UPDATE_INTERVAL", "3"))
SIM_SPEED_MULTIPLIER = float(os.getenv("GPS_SIM_SPEED_MULT", "8.0"))
GRID_RESOLUTION = 10
GPS_CAMERA_ID = "GPS_AGGREGATOR"
VISUALIZATION_TOPIC = os.getenv("GPS_SIM_VIS_TOPIC", "stadium/visualization/people")

STADIUM_OFFSET_X = float(os.getenv("GPS_SIM_OFFSET_X", "0"))
STADIUM_OFFSET_Y = float(os.getenv("GPS_SIM_OFFSET_Y", "0"))

# Dragão / UA base coordinates
BASE_LAT = 40.6300
BASE_LNG = -8.6558

# Gaussian smoothing sigma (in grid cells) for heatmap
GRID_SMOOTH_SIGMA = 1.5

# ============================================================
# UA Campus POIs — Real locations relative to BASE_LAT/BASE_LNG
# Coordinates in meters. weight = relative popularity.
# radius = area around POI where people cluster (meters).
# pause_range = (min, max) seconds a walker pauses at the POI.
# ============================================================
UA_POIS = [
    # Departamentos de Engenharia (norte do campus)
    {"name": "DETI",              "x": -50,   "y": 320,  "weight": 1.0, "radius": 45, "pause_range": (60, 300)},
    {"name": "DECA",              "x": 350,   "y": -80,  "weight": 1.0, "radius": 35, "pause_range": (60, 240)},
    {"name": "DEMEC",             "x": -120,  "y": 280,  "weight": 1.0, "radius": 30, "pause_range": (60, 240)},
    {"name": "DeCivil",           "x": 20,    "y": 380,  "weight": 1.0, "radius": 30, "pause_range": (60, 240)},

    # Zona central
    {"name": "Reitoria",          "x": 0,     "y": 120,  "weight": 1.0, "radius": 25, "pause_range": (30, 120)},
    {"name": "Biblioteca",        "x": -200,  "y": 60,   "weight": 1.0, "radius": 30, "pause_range": (120, 600)},
    {"name": "SACUA",             "x": -80,   "y": 80,   "weight": 1.0, "radius": 20, "pause_range": (30, 180)},

    # Alimentação
    {"name": "Cantina Santiago",  "x": 50,    "y": 50,   "weight": 1.0, "radius": 40, "pause_range": (600, 1800)},
    {"name": "Cantina Crasto",    "x": -100,  "y": -300, "weight": 1.0, "radius": 30, "pause_range": (600, 1200)},
    {"name": "Bar DETI",          "x": -30,   "y": 310,  "weight": 1.0, "radius": 15, "pause_range": (120, 600)},

    # Departamentos de Ciências
    {"name": "Departamento Física",     "x": 180,  "y": 200,  "weight": 1.0, "radius": 30, "pause_range": (60, 300)},
    {"name": "Departamento Química",    "x": 250,  "y": 150,  "weight": 1.0, "radius": 30, "pause_range": (60, 300)},
    {"name": "Departamento Biologia",   "x": 300,  "y": 50,   "weight": 1.0, "radius": 30, "pause_range": (60, 300)},
    {"name": "Departamento Matemática", "x": 100,  "y": 250,  "weight": 1.0, "radius": 25, "pause_range": (60, 240)},

    # Residências
    {"name": "Residências Santiago", "x": 120,  "y": -150, "weight": 1.0, "radius": 50, "pause_range": (300, 1800)},
    {"name": "Residências Crasto",   "x": -180, "y": -350, "weight": 1.0, "radius": 50, "pause_range": (300, 1800)},

    # Entradas / Portões (pontos de chegada/saída, menor pausa)
    {"name": "Entrada Principal",  "x": -300, "y": -50,  "weight": 1.0, "radius": 20, "pause_range": (5, 30)},
    {"name": "Entrada Norte",      "x": -100, "y": 450,  "weight": 1.0, "radius": 20, "pause_range": (5, 30)},
    {"name": "Entrada Este",       "x": 420,  "y": 0,    "weight": 1.0, "radius": 20, "pause_range": (5, 30)},

    # Espaços verdes / zonas de lazer
    {"name": "Lago Central",      "x": 80,   "y": 160,  "weight": 1.0, "radius": 35, "pause_range": (60, 600)},
    {"name": "Jardim Crasto",     "x": -50,  "y": -200, "weight": 1.0, "radius": 30, "pause_range": (60, 300)},

    # ISCA (sul)
    {"name": "ISCA",              "x": 380,  "y": -200, "weight": 1.0, "radius": 35, "pause_range": (60, 300)},
]

# Normalize weights
_total_weight = sum(p["weight"] for p in UA_POIS)
for p in UA_POIS:
    p["weight"] /= _total_weight

# Pre-compute cumulative weights for weighted random selection
_cum_weights = []
_cum = 0.0
for p in UA_POIS:
    _cum += p["weight"]
    _cum_weights.append(_cum)


def choose_random_poi(exclude_idx=None):
    """Pick a random POI using weighted probabilities, optionally excluding one."""
    while True:
        r = random.random()
        for i, cw in enumerate(_cum_weights):
            if r <= cw:
                if exclude_idx is not None and i == exclude_idx:
                    break  # retry
                return i
        # Fallback (shouldn't happen due to float rounding, but safe)
        idx = len(UA_POIS) - 1
        if exclude_idx is None or idx != exclude_idx:
            return idx


def meters_to_latlng(dx, dy):
    lat = BASE_LAT + (dy / 111000)
    lng = BASE_LNG + (dx / (111000 * 0.752))
    return lat, lng


# ============================================================
# Walker — Realistic pedestrian agent
# ============================================================
USER_IDS = [str(uuid.uuid4()) for _ in range(NUM_USERS)]


def build_walker(uid, idx):
    """Create a walker agent that moves organically between campus POIs."""
    # Start near a random POI
    start_poi_idx = idx % len(UA_POIS)
    poi = UA_POIS[start_poi_idx]

    # Position with random offset within the POI radius
    angle = random.uniform(0, 2 * math.pi)
    dist = random.uniform(0, poi["radius"] * 0.8)
    start_x = poi["x"] + dist * math.cos(angle)
    start_y = poi["y"] + dist * math.sin(angle)

    lat, lng = meters_to_latlng(start_x, start_y)

    # Choose a destination POI (different from start)
    dest_idx = choose_random_poi(exclude_idx=start_poi_idx)

    return {
        "user_id": uid,
        "x": float(start_x),
        "y": float(start_y),
        "lat": float(lat),
        "lng": float(lng),
        # Movement parameters (vary per walker for diversity)
        "speed_mps": random.uniform(1.0, 1.8),    # walking speed (m/s)
        "jitter_sigma": random.uniform(0.5, 2.5),  # lateral noise (meters)
        # State machine
        "state": "walking",           # "walking" or "paused"
        "pause_remaining": 0.0,       # seconds left in pause
        "current_poi_idx": start_poi_idx,
        "dest_poi_idx": dest_idx,
        # Intermediate target (smoothed path point)
        "target_x": None,
        "target_y": None,
    }


def _pick_target_near_poi(poi):
    """Pick a random point within a POI's area."""
    angle = random.uniform(0, 2 * math.pi)
    dist = random.uniform(0, poi["radius"] * 0.7)
    return poi["x"] + dist * math.cos(angle), poi["y"] + dist * math.sin(angle)


def advance_walker(walker, delta_seconds):
    """Advance a walker by one time step."""

    # --- Paused state ---
    if walker["state"] == "paused":
        walker["pause_remaining"] -= delta_seconds * SIM_SPEED_MULTIPLIER
        if walker["pause_remaining"] <= 0:
            # Done pausing — pick a new destination
            walker["state"] = "walking"
            old_dest = walker["dest_poi_idx"]
            walker["current_poi_idx"] = old_dest
            walker["dest_poi_idx"] = choose_random_poi(exclude_idx=old_dest)
            walker["target_x"] = None
            walker["target_y"] = None
        else:
            # Small idle drift (people shuffle around while waiting)
            walker["x"] += random.gauss(0, 0.3) * delta_seconds * SIM_SPEED_MULTIPLIER
            walker["y"] += random.gauss(0, 0.3) * delta_seconds * SIM_SPEED_MULTIPLIER
            walker["lat"], walker["lng"] = meters_to_latlng(walker["x"], walker["y"])
            return

    # --- Walking state ---
    dest_poi = UA_POIS[walker["dest_poi_idx"]]

    # Set intermediate target if needed
    if walker["target_x"] is None:
        walker["target_x"], walker["target_y"] = _pick_target_near_poi(dest_poi)

    dx = walker["target_x"] - walker["x"]
    dy = walker["target_y"] - walker["y"]
    distance = math.hypot(dx, dy)

    # Arrived at destination?
    arrival_threshold = dest_poi["radius"] * 0.3
    if distance < arrival_threshold:
        # Arrived — pause at this POI
        walker["state"] = "paused"
        pmin, pmax = dest_poi["pause_range"]
        walker["pause_remaining"] = random.uniform(pmin, pmax)
        return

    # Move towards target with organic jitter
    step = walker["speed_mps"] * delta_seconds * SIM_SPEED_MULTIPLIER

    if distance > 0:
        # Direction towards target
        dir_x = dx / distance
        dir_y = dy / distance

        # Add perpendicular Gaussian jitter for organic paths
        perp_x = -dir_y
        perp_y = dir_x
        jitter = random.gauss(0, walker["jitter_sigma"]) * delta_seconds * SIM_SPEED_MULTIPLIER
        
        # Slow down when approaching destination (last 20m)
        speed_factor = min(1.0, distance / 20.0) * 0.5 + 0.5

        actual_step = min(step * speed_factor, distance)
        walker["x"] += dir_x * actual_step + perp_x * jitter
        walker["y"] += dir_y * actual_step + perp_y * jitter

    walker["lat"], walker["lng"] = meters_to_latlng(walker["x"], walker["y"])

WALKERS = [build_walker(uid, i) for i, uid in enumerate(USER_IDS)]

# ============================================================
# ROIs (Queue regions)
# ============================================================
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


# ============================================================
# MQTT Client
# ============================================================
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


# ============================================================
# Point-in-polygon (ray casting)
# ============================================================
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


# ============================================================
# Gaussian-smoothed grid aggregation
# ============================================================
def gaussian_weight(dist_sq, sigma_sq):
    """Unnormalized Gaussian weight."""
    return math.exp(-0.5 * dist_sq / sigma_sq)


def build_crowd_density_event(active_walkers):
    """
    Build a crowd density event using Gaussian-smoothed grid.
    
    Instead of simply incrementing the cell where each walker stands,
    we distribute each walker's contribution across nearby grid cells
    using a 2D Gaussian kernel. This produces smooth, organic heatmaps
    instead of blocky rectangles.
    """
    points_xy = []
    grid_counts = {}
    sigma = GRID_SMOOTH_SIGMA * GRID_RESOLUTION  # sigma in meters
    sigma_sq = sigma * sigma
    # How many cells away to spread the Gaussian (truncated at ~3 sigma)
    spread_cells = int(math.ceil(3.0 * GRID_SMOOTH_SIGMA))

    for walker in active_walkers:
        wx, wy = walker["x"], walker["y"]
        points_xy.append((wx, wy))

        # Grid cell of this walker
        cell_ix = int(math.floor(wx / GRID_RESOLUTION))
        cell_iy = int(math.floor(wy / GRID_RESOLUTION))

        # Distribute across nearby cells
        for di in range(-spread_cells, spread_cells + 1):
            for dj in range(-spread_cells, spread_cells + 1):
                ci = cell_ix + di
                cj = cell_iy + dj

                # Cell center in meters
                cx = (ci * GRID_RESOLUTION) + (GRID_RESOLUTION / 2) + STADIUM_OFFSET_X
                cy = (cj * GRID_RESOLUTION) + (GRID_RESOLUTION / 2) + STADIUM_OFFSET_Y

                dist_sq = (wx - cx + STADIUM_OFFSET_X) ** 2 + (wy - cy + STADIUM_OFFSET_Y) ** 2
                w = gaussian_weight(dist_sq, sigma_sq)

                if w > 0.01:  # Ignore negligible contributions
                    cell_key = (cx, cy)
                    grid_counts[cell_key] = grid_counts.get(cell_key, 0.0) + w

    # Convert to integer counts (round, min 1 for occupied cells)
    grid_data = []
    for (gx, gy), raw_count in grid_counts.items():
        count = max(1, int(round(raw_count)))
        grid_data.append({"x": gx, "y": gy, "count": count})

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


# ============================================================
# Queue events
# ============================================================
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


# ============================================================
# Main loop
# ============================================================
def main():
    client = create_client()
    print(f"GPS Simulator (UA Campus) conectado a {MQTT_BROKER}:{MQTT_PORT}")
    print(f"   Tópico GPS:        {TOPIC}")
    print(f"   Tópico Congestion: {CONGESTION_TOPIC}")
    print(f"   Tópico Visualização: {VISUALIZATION_TOPIC}")
    print(f"   {NUM_USERS} utilizadores simulados, {ACTIVE_PER_TICK} ativos por ciclo")
    print(f"   {len(UA_POIS)} POIs do campus UA definidos")
    print(f"   Grid resolution: {GRID_RESOLUTION}m, Gaussian sigma: {GRID_SMOOTH_SIGMA} cells")
    print()

    # Show POI summary
    for poi in UA_POIS:
        print(f"   POI: {poi['name']:25s}  ({poi['x']:+6.0f}, {poi['y']:+6.0f})  peso={poi['weight']:.3f}  r={poi['radius']}m")
    print()

    try:
        last_tick = time.time()
        tick_count = 0
        while True:
            now = time.time()
            delta_seconds = max(now - last_tick, 0.1)

            # Select active walkers this round
            active_this_round = random.sample(WALKERS, min(ACTIVE_PER_TICK, len(WALKERS)))

            # Advance each walker
            for walker in active_this_round:
                advance_walker(walker, delta_seconds)

                # Publish individual GPS update
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

            # Publish visualization payload (per-user positions for frontend)
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

            # Build and publish crowd density event (Gaussian-smoothed)
            crowd_event, points_xy = build_crowd_density_event(active_this_round)
            client.publish(CONGESTION_TOPIC, json.dumps(crowd_event), qos=0)

            # Publish queue events
            publish_queue_events(client, points_xy)

            # Log summary
            tick_count += 1
            walking = sum(1 for w in active_this_round if w["state"] == "walking")
            paused = sum(1 for w in active_this_round if w["state"] == "paused")
            cells = len(crowd_event["grid_data"])
            print(
                f"[tick {tick_count:4d}] {len(active_this_round)} users "
                f"({walking} walking, {paused} paused) "
                f"-> {cells} grid cells publicados"
            )

            last_tick = time.time()
            time.sleep(UPDATE_INTERVAL)

    except KeyboardInterrupt:
        print("\nSaindo...")
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()

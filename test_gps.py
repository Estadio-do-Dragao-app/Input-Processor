import paho.mqtt.client as mqtt
import json
import time
import random
import uuid

# Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
TOPIC = "stadium/location/gps"

# Create a fixed set of fake user IDs
USER_IDS = [str(uuid.uuid4()) for _ in range(50)]

# Dragão base coordinates for walking simulation
# Somewhere near stadium
BASE_LAT = 41.1617
BASE_LNG = -8.5836

def create_client():
    client = mqtt.Client(client_id=f"gps_mock_{uuid.uuid4().hex[:6]}")
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    return client

def main():
    client = create_client()
    print(f"📡 Conectado mock a {MQTT_BROKER}:{MQTT_PORT} no tópico {TOPIC}")
    
    try:
        while True:
            # Pick a random subset of users to update this tick
            active_this_round = random.sample(USER_IDS, 25)
            
            for uid in active_this_round:
                # Add some random walk variance to base coords
                # Rough approximation: 0.0001 is ~10 meters
                lat = BASE_LAT + random.uniform(-0.0005, 0.0005)
                lng = BASE_LNG + random.uniform(-0.0005, 0.0005)
                
                payload = {
                    "user_id": uid,
                    "lat": lat,
                    "lng": lng,
                    "timestamp": time.time()
                }
                
                client.publish(TOPIC, json.dumps(payload))
                
            print(f"📍 Publicados {len(active_this_round)} eventos GPS falsos.")
            time.sleep(2)
            
    except KeyboardInterrupt:
        print("\nSaindo...")
        client.disconnect()

if __name__ == "__main__":
    main()

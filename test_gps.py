import paho.mqtt.client as mqtt
import json
import time
import random
import uuid

# Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
TOPIC = "stadium/location/gps"

# Create a larger set of fake user IDs
USER_IDS = [str(uuid.uuid4()) for _ in range(1000)]

# Dragão base coordinates - Expanded to UA Campus area
BASE_LAT = 40.6300
BASE_LNG = -8.6558

def create_client():
    client = mqtt.Client(client_id=f"gps_mock_{uuid.uuid4().hex[:6]}")
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client

def main():
    client = create_client()
    print(f"Conectado mock a {MQTT_BROKER}:{MQTT_PORT} no tópico {TOPIC}")
    
    try:
        while True:
            # Pick a larger subset of users to update this tick for higher density
            active_this_round = random.sample(USER_IDS, 500)
            
            for uid in active_this_round:
                # Expanded range to cover the entire UA Campus (+/- 0.005 deg ~ 500m)
                lat = BASE_LAT + random.uniform(-0.005, 0.005)
                lng = BASE_LNG + random.uniform(-0.005, 0.005)
                
                payload = {
                    "user_id": uid,
                    "lat": lat,
                    "lng": lng,
                    "timestamp": time.time()
                }
                
                client.publish(TOPIC, json.dumps(payload))
                
            print(f"Publicados {len(active_this_round)} eventos GPS falsos.")
            time.sleep(10) # Slower updates for better performance
            
    except KeyboardInterrupt:
        print("\nSaindo...")
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()

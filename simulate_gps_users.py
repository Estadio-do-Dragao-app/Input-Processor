import os
import json
import time
import random
import paho.mqtt.client as mqtt

# Configurações MQTT (ajuste conforme necessário)
MQTT_BROKER = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_BROKER_PORT", 1883))
GPS_TOPIC = "stadium/location/gps"

# Limites do estádio (aproximados, ajuste conforme necessário)
# Universidade de Aveiro coordinates (matching gps_processor.py)
BASE_LAT = 40.6300
BASE_LNG = -8.6558
LAT_RANGE = 0.008
LNG_RANGE = 0.008

# Simulação de utilizadores
NUM_USERS = 10
USER_IDS = [f"user_{i+1}" for i in range(NUM_USERS)]

# Gera coordenadas aleatórias próximas do estádio
def random_lat_lng():
    lat = BASE_LAT + random.uniform(-LAT_RANGE/2, LAT_RANGE/2)
    lng = BASE_LNG + random.uniform(-LNG_RANGE/2, LNG_RANGE/2)
    return lat, lng

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"✅ Simulador: Conectado ao MQTT {MQTT_BROKER}:{MQTT_PORT}")
    else:
        print(f"❌ Simulador: Falha ao conectar (código {rc})")

def main():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()

    try:
        while True:
            for user_id in USER_IDS:
                lat, lng = random_lat_lng()
                payload = {
                    "user_id": user_id,
                    "lat": lat,
                    "lng": lng
                }
                client.publish(GPS_TOPIC, json.dumps(payload))
                print(f"📡 Enviado: {payload}")
                time.sleep(0.2)  # Pequeno atraso entre utilizadores
            time.sleep(5)  # Espera antes de nova ronda de updates
    except KeyboardInterrupt:
        print("\n🛑 Simulação terminada.")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()

cd ~/Documents/aqi-email-bot
cat > collect_aqi.py << 'EOF'
import requests
import sqlite3
import os
import logging
from datetime import datetime, timedelta

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# ENV VARIABLES
# =========================
API_KEY = os.getenv("API_KEY")
DB_PATH = "aqi_history.db"

if not API_KEY:
    logger.error("Missing API_KEY environment variable")
    exit(1)

# =========================
# LOCATIONS
# =========================
locations = [
    {"name": "Calamba, Laguna", "lat": 14.1919, "lon": 121.0711},
    {"name": "Biñan, Laguna", "lat": 14.2769, "lon": 121.0589},
]

PH_OFFSET = timedelta(hours=8)

# =========================
# PM2.5 → AQI (EPA Formula)
# =========================
def pm25_to_aqi(pm25):
    if pm25 is None or pm25 < 0:
        return 0
    breakpoints = [
        (0.0,   12.0,   0,   50),
        (12.1,  35.4,  51,  100),
        (35.5,  55.4, 101,  150),
        (55.5, 150.4, 151,  200),
        (150.5, 250.4, 201, 300),
        (250.5, 350.4, 301, 400),
        (350.5, 500.4, 401, 500),
    ]
    for c_low, c_high, aqi_low, aqi_high in breakpoints:
        if c_low <= pm25 <= c_high:
            return round(((aqi_high - aqi_low) / (c_high - c_low)) * (pm25 - c_low) + aqi_low)
    return 500

# =========================
# FETCH AQI
# =========================
def get_aqi_data(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        pm2_5 = data["list"][0]["components"].get("pm2_5")
        return pm2_5
    except Exception as e:
        logger.error(f"Failed to fetch AQI for ({lat}, {lon}): {e}")
        return None

# =========================
# STORE IN DATABASE
# =========================
def store_aqi_reading(location_name, pm2_5):
    """Store AQI reading in SQLite"""
    if pm2_5 is None:
        logger.warning(f"Skipping {location_name} - no PM2.5 data")
        return False
    
    aqi = pm25_to_aqi(pm2_5)
    today = (datetime.utcnow() + PH_OFFSET).strftime("%Y-%m-%d")
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO aqi_readings (date, location, pm2_5, aqi)
            VALUES (?, ?, ?, ?)
        """, (today, location_name, pm2_5, aqi))
        conn.commit()
        conn.close()
        logger.info(f"Stored: {location_name} | {today} | PM2.5: {pm2_5} | AQI: {aqi}")
        return True
    except Exception as e:
        logger.error(f"Database error for {location_name}: {e}")
        return False

# =========================
# MAIN
# =========================
def collect_aqi():
    logger.info("Starting AQI data collection...")
    for loc in locations:
        pm2_5 = get_aqi_data(loc["lat"], loc["lon"])
        store_aqi_reading(loc["name"], pm2_5)
    logger.info("AQI data collection complete")

if __name__ == "__main__":
    collect_aqi()
EOF
import requests
import os
import logging
from datetime import datetime, timedelta
import firebase_admin
from firebase_admin import db, credentials

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# ENV VARIABLES
# =========================
IQAIR_API_KEY = os.getenv("IQAIR_API_KEY")
FIREBASE_KEY = "firebase-key.json"
FIREBASE_URL = "https://aqi-email-bot-default-rtdb.asia-southeast1.firebasedatabase.app"

if not IQAIR_API_KEY:
    logger.error("Missing IQAIR_API_KEY environment variable")
    exit(1)

# =========================
# FIREBASE SETUP
# =========================
try:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_URL})
except ValueError:
    pass

# =========================
# LOCATIONS
# =========================
locations = [
    {"name": "Calamba Laguna", "lat": 14.195, "lon": 121.065},  # Correct Calamba station coordinates
]

# Binan uses official API city coordinates (from /city endpoint, AQI ~65)
binan_station = {
    "name": "Binan Laguna", "lat": 14.2655, "lon": 121.4338
}

PH_OFFSET = timedelta(hours=8)

# =========================
# FETCH AQI FROM IQAIR
# =========================
def get_aqi_from_iqair(lat, lon):
    try:
        url = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={IQAIR_API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "success":
            logger.error(f"IQAir API error: {data.get('data')}")
            return None

        aqi = data["data"]["current"]["pollution"]["aqius"]
        logger.info(f"IQAir AQI: {aqi}")
        return aqi
    except Exception as e:
        logger.error(f"Failed to fetch AQI from IQAir for ({lat}, {lon}): {e}")
        return None

# =========================
# STORE 5-MINUTE READING IN FIREBASE
# =========================
def store_reading(location_name, aqi):
    """Store AQI reading with 5-minute precision"""
    if aqi is None:
        logger.warning(f"Skipping {location_name} - no AQI data")
        return False

    now_ph   = datetime.utcnow() + PH_OFFSET
    date_key = now_ph.strftime("%Y-%m-%d")
    time_key = now_ph.strftime("%H:%M")  # Now includes minutes (HH:MM format)

    try:
        ref = db.reference(f"aqi_hourly/{location_name}/{date_key}/{time_key}")
        ref.set({
            "aqi":       aqi,
            "timestamp": now_ph.isoformat()
        })
        logger.info(f"Stored: {location_name} | {date_key} {time_key} | AQI: {aqi}")
        return True
    except Exception as e:
        logger.error(f"Firebase error for {location_name}: {e}")
        return False

# =========================
# CLEANUP OLD DATA (>30 days)
# =========================
def cleanup_old_data(days=180):
    try:
        cutoff = (datetime.utcnow() + PH_OFFSET - timedelta(days=days)).strftime("%Y-%m-%d")
        for loc in locations:
            ref  = db.reference(f"aqi_hourly/{loc['name']}")
            data = ref.get()
            if data:
                for date_str in list(data.keys()):
                    if date_str < cutoff:
                        db.reference(f"aqi_hourly/{loc['name']}/{date_str}").delete()
                        logger.info(f"Deleted old data: {loc['name']} | {date_str}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

# =========================
# MAIN
# =========================
def collect_aqi():
    logger.info("Starting 5-minute AQI collection (IQAir)...")
    
    # Collect Calamba data
    for loc in locations:
        aqi = get_aqi_from_iqair(loc["lat"], loc["lon"])
        store_reading(loc["name"], aqi)
    
    # Collect Binan data
    binan_aqi = get_aqi_from_iqair(binan_station["lat"], binan_station["lon"])
    store_reading(binan_station["name"], binan_aqi)
    
    cleanup_old_data(days=30)
    logger.info("AQI collection complete")

if __name__ == "__main__":
    collect_aqi()
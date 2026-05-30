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
    {"name": "Calamba Laguna", "lat": 14.217528, "lon": 121.064056},  # Treveia Nuvali station
]

# Binan uses two stations that we'll average
binan_stations = [
    {"name": "Unioil Lucban", "lat": 14.1156, "lon": 121.5554},
    {"name": "Unioil San Francisco Halang Rd", "lat": 14.2769, "lon": 121.0589},
]

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
# AVERAGE AQI FROM MULTIPLE SOURCES
# =========================
def average_aqi_values(aqi_list):
    """Average AQI values from multiple stations"""
    valid_aqi = [aqi for aqi in aqi_list if aqi is not None]
    if not valid_aqi:
        return None
    return int(sum(valid_aqi) / len(valid_aqi))

# =========================
# STORE HOURLY READING IN FIREBASE
# =========================
def store_hourly_reading(location_name, aqi):
    if aqi is None:
        logger.warning(f"Skipping {location_name} - no AQI data")
        return False

    now_ph   = datetime.utcnow() + PH_OFFSET
    date_key = now_ph.strftime("%Y-%m-%d")
    hour_key = now_ph.strftime("%H")

    try:
        ref = db.reference(f"aqi_hourly/{location_name}/{date_key}/{hour_key}")
        ref.set({
            "aqi":       aqi,
            "timestamp": now_ph.isoformat()
        })
        logger.info(f"Stored: {location_name} | {date_key} {hour_key}:00 | AQI: {aqi}")
        return True
    except Exception as e:
        logger.error(f"Firebase error for {location_name}: {e}")
        return False

# =========================
# CLEANUP OLD DATA (>30 days)
# =========================
def cleanup_old_data(days=30):
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
    logger.info("Starting hourly AQI collection (IQAir)...")
    
    # Collect Calamba data
    for loc in locations:
        aqi = get_aqi_from_iqair(loc["lat"], loc["lon"])
        store_hourly_reading(loc["name"], aqi)
    
    # Collect Binan data (average from both Unioil stations)
    binan_aqi_list = [get_aqi_from_iqair(station["lat"], station["lon"]) for station in binan_stations]
    binan_averaged_aqi = average_aqi_values(binan_aqi_list)
    store_hourly_reading("Biñan Laguna", binan_averaged_aqi)
    
    cleanup_old_data(days=30)
    logger.info("AQI collection complete")

if __name__ == "__main__":
    collect_aqi()
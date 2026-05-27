import requests
import os
import logging
import time
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
API_KEY = os.getenv("API_KEY")
FIREBASE_KEY = "firebase-key.json"
FIREBASE_URL = "https://aqi-email-bot-default-rtdb.asia-southeast1.firebasedatabase.app"

if not API_KEY:
    logger.error("Missing API_KEY environment variable")
    exit(1)

# =========================
# FIREBASE SETUP
# =========================
try:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_URL})
    logger.info("Firebase initialized")
except ValueError:
    logger.info("Firebase already initialized")
except Exception as e:
    logger.error(f"Firebase initialization failed: {e}")
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
# GET AQI HISTORY FROM OPENWEATHERMAP
# =========================
def get_aqi_history(lat, lon, days=30):
    """Fetch hourly AQI history for past N days"""
    end = int(time.time())
    start = int((datetime.utcnow() - timedelta(days=days)).timestamp())
    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/air_pollution/history"
            f"?lat={lat}&lon={lon}&start={start}&end={end}&appid={API_KEY}"
        )
        logger.info(f"Fetching history: {url[:80]}...")
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json().get("list", [])
        logger.info(f"Got {len(data)} hourly records")
        return data
    except Exception as e:
        logger.error(f"Failed to fetch AQI history for ({lat}, {lon}): {e}")
        return []

# =========================
# COMPUTE DAILY AVERAGE
# =========================
def compute_daily_average(history_list):
    """
    Calculate daily average PM2.5 from hourly data.
    Convert average PM2.5 to AQI.
    Returns dict of {date: {"pm2_5": avg, "aqi": aqi_value}}
    """
    daily = {}
    
    for entry in history_list:
        # Convert UTC timestamp to PH timezone
        dt_utc = datetime.utcfromtimestamp(entry["dt"])
        dt_ph = dt_utc + PH_OFFSET
        day_key = dt_ph.strftime("%Y-%m-%d")
        
        pm2_5 = entry.get("components", {}).get("pm2_5")
        if pm2_5 is None or pm2_5 < 0:
            pm2_5 = 0
        
        if day_key not in daily:
            daily[day_key] = {"readings": []}
        
        daily[day_key]["readings"].append(pm2_5)
    
    # Calculate daily average and convert to AQI
    result = {}
    for day_key in sorted(daily.keys()):
        readings = daily[day_key]["readings"]
        avg_pm25 = sum(readings) / len(readings)
        aqi_value = pm25_to_aqi(avg_pm25)
        
        result[day_key] = {
            "pm2_5": round(avg_pm25, 2),
            "aqi": aqi_value
        }
    
    return result

# =========================
# STORE IN FIREBASE
# =========================
def store_in_firebase(location_name, daily_data):
    """Store daily data in Firebase"""
    stored_count = 0
    try:
        for date_str, data in daily_data.items():
            ref = db.reference(f"aqi_readings/{location_name}/{date_str}")
            ref.set({
                "pm2_5": data["pm2_5"],
                "aqi": data["aqi"]
            })
            logger.info(f"  ✓ {date_str} | PM2.5: {data['pm2_5']} | AQI: {data['aqi']}")
            stored_count += 1
        
        logger.info(f"Stored {stored_count} days for {location_name}")
        return stored_count
    except Exception as e:
        logger.error(f"Firebase error for {location_name}: {e}")
        return 0

# =========================
# MAIN BACKFILL
# =========================
def backfill():
    logger.info("="*60)
    logger.info("FIREBASE BACKFILL - 30 DAYS")
    logger.info("="*60)
    
    total_stored = 0
    
    for loc in locations:
        logger.info(f"\nProcessing: {loc['name']}")
        logger.info("-" * 60)
        
        # Fetch history
        history = get_aqi_history(loc["lat"], loc["lon"], days=30)
        if not history:
            logger.warning(f"No data returned for {loc['name']}")
            continue
        
        # Compute daily averages
        daily_data = compute_daily_average(history)
        logger.info(f"Computed {len(daily_data)} days of data")
        
        # Store in Firebase
        stored = store_in_firebase(loc["name"], daily_data)
        total_stored += stored
    
    logger.info("\n" + "="*60)
    logger.info(f"BACKFILL COMPLETE - {total_stored} total records stored")
    logger.info("="*60)

if __name__ == "__main__":
    backfill()
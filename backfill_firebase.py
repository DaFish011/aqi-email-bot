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
except ValueError:
    pass

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
# GET AQI HISTORY
# =========================
def get_aqi_history(lat, lon, days=30):
    """Fetch 30-day history from OpenWeatherMap"""
    import time
    end = int(time.time())
    start = int((datetime.utcnow() - timedelta(days=days)).timestamp())
    try:
        url = (
            f"https://api.openweathermap.org/data/2.5/air_pollution/history"
            f"?lat={lat}&lon={lon}&start={start}&end={end}&appid={API_KEY}"
        )
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json().get("list", [])
    except Exception as e:
        logger.error(f"Failed to fetch AQI history for ({lat}, {lon}): {e}")
        return []

# =========================
# COMPUTE DAILY AVERAGE
# =========================
def compute_daily_average(history_list):
    """Calculate daily average PM2.5 from hourly data"""
    daily = {}
    for entry in history_list:
        dt_utc = datetime.utcfromtimestamp(entry["dt"])
        dt_ph = dt_utc + PH_OFFSET
        day_key = dt_ph.strftime("%Y-%m-%d")
        pm2_5 = entry.get("components", {}).get("pm2_5") or 0
        
        if day_key not in daily:
            daily[day_key] = []
        daily[day_key].append(pm2_5)
    
    result = {}
    for day_key, values in daily.items():
        avg_pm25 = sum(values) / len(values)
        result[day_key] = avg_pm25
    
    return result

# =========================
# BACKFILL FIREBASE
# =========================
def backfill_firebase():
    """Fetch 30-day history and populate Firebase"""
    logger.info("Starting Firebase backfill...")
    
    for loc in locations:
        logger.info(f"Fetching history for {loc['name']}...")
        history = get_aqi_history(loc["lat"], loc["lon"], days=30)
        
        if not history:
            logger.warning(f"No history data for {loc['name']}")
            continue
        
        daily_data = compute_daily_average(history)
        
        for date_str, pm2_5 in daily_data.items():
            aqi = pm25_to_aqi(pm2_5)
            try:
                ref = db.reference(f"aqi_readings/{loc['name']}/{date_str}")
                ref.set({
                    "pm2_5": round(pm2_5, 2),
                    "aqi": aqi
                })
                logger.info(f"  {date_str} | {loc['name']} | PM2.5: {pm2_5:.2f} | AQI: {aqi}")
            except Exception as e:
                logger.error(f"Error storing data: {e}")
        
        logger.info(f"Completed {loc['name']}")
    
    logger.info("Firebase backfill complete!")

if __name__ == "__main__":
    backfill_firebase()
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import math
import time
import json
import urllib.parse
from datetime import datetime, timedelta, timezone

# =========================
# LOGGING & CONFIG
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
PH_OFFSET = timedelta(hours=8)
TAAL_LAT, TAAL_LON = 14.3568, 121.0064
LOCATIONS = [
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
]

# Env Variables
API_KEY = os.getenv("API_KEY")
SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
RECEIVERS_STR = os.getenv("RECEIVERS")

if not all([API_KEY, SENDER, PASSWORD, RECEIVERS_STR]):
    logger.error("Missing required environment variables.")
    exit(1)

RECEIVERS = [email.strip() for email in RECEIVERS_STR.split(",")]

# =========================
# HELPER FUNCTIONS
# =========================

def pm25_to_aqi(pm25):
    if pm25 is None or pm25 < 0: return 0
    breakpoints = [
        (0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100),
        (35.5, 55.4, 101, 150), (55.5, 150.4, 151, 200),
        (150.5, 250.4, 201, 300), (250.5, 350.4, 301, 400),
        (350.5, 500.4, 401, 500)
    ]
    for c_low, c_high, aqi_low, aqi_high in breakpoints:
        if c_low <= pm25 <= c_high:
            return round(((aqi_high - aqi_low) / (c_high - c_low)) * (pm25 - c_low) + aqi_low)
    return 500

def get_aqi_data(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()["list"][0]
        return {"aqi": data["main"]["aqi"], **data["components"]}
    except Exception as e:
        logger.error(f"AQI Fetch Error: {e}")
        return None

def get_weather_data(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        w = resp.json().get("current_weather", {})
        return {"temp": w.get("temperature"), "wind_speed": w.get("windspeed"), "wind_deg": w.get("winddirection")}
    except Exception as e:
        logger.error(f"Weather Fetch Error: {e}")
        return None

# =========================
# WIND & BEARING LOGIC
# =========================

def get_wind_direction(deg):
    if deg is None: return "-"
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((float(deg) + 22.5) / 45) % 8]

def get_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

# =========================
# EMAIL & CHART BUILDING
# =========================

# (Keeping your existing build_trend_chart_url and compute_daily_data as they are quite robust)
# [ ... Insert your existing Chart and Label merging functions here ... ]

def send_email():
    try:
        html_email = build_html_email()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🌍 Air Quality Report: {datetime.now(timezone.utc).astimezone().strftime('%b %d')}"
        msg["From"] = SENDER
        msg["To"] = ", ".join(RECEIVERS)
        msg.attach(MIMEText(html_email, "html"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        logger.info("Report delivered successfully.")
    except Exception as e:
        logger.error(f"Email Dispatch Failed: {e}")

if __name__ == "__main__":
    send_email()

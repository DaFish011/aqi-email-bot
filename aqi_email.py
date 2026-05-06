import requests
import smtplib
from email.mime.text import MIMEText
import os
import logging

# =========================
# LOGGING SETUP
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# ENV VARIABLES
# =========================
API_KEY = os.getenv("API_KEY")
SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")

# Validate required environment variables
if not API_KEY:
    logger.error("API_KEY not set")
if not SENDER:
    logger.error("EMAIL_USER not set")
if not PASSWORD:
    logger.error("EMAIL_PASS not set")

RECEIVERS = [
    "verdegan011@gmail.com",
    "kroderno011@gmail.com"
]

# =========================
# LOCATIONS
# =========================
locations = [
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
]

# =========================
# AQI LABELS
# =========================
aqi_map = {
    1: "Good 🟢",
    2: "Fair 🟡",
    3: "Moderate 🟠",
    4: "Poor 🔴",
    5: "Very Poor 🟣"
}

# =========================
# AQI FUNCTION (OPENWEATHER)
# =========================
def get_aqi_data(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        m = data["list"][0]["main"]
        c = data["list"][0]["components"]

        return {
            "aqi": m.get("aqi"),
            "aqi_text": aqi_map.get(m.get("aqi"), "Unknown"),
            "pm2_5": c.get("pm2_5"),
            "pm10": c.get("pm10"),
            "no2": c.get("no2"),
            "o3": c.get("o3"),
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching AQI data for ({lat}, {lon}): {e}")
        return {
            "aqi": "-",
            "aqi_text": "No data",
            "pm2_5": "-",
            "pm10": "-",
            "no2": "-",
            "o3": "-"
        }
    except (KeyError, IndexError) as e:
        logger.error(f"Error parsing AQI response: {e}")
        return {
            "aqi": "-",
            "aqi_text": "No data",
            "pm2_5": "-",
            "pm10": "-",
            "no2": "-",
            "o3": "-"
        }

# =========================
# WEATHER FUNCTION (OPEN-METEO)
# =========================
def get_weather_data(lat, lon):
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current_weather=true"
        )

        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        w = data.get("current_weather")

        if not w:
            logger.warning(f"No weather data for ({lat}, {lon})")
            return None

        return {
            "temp": w.get("temperature"),
            "wind_speed": w.get("windspeed"),
            "wind_deg": w.get("winddirection")
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching weather data for ({lat}, {lon}): {e}")
        return None
    except (KeyError, ValueError) as e:
        logger.error(f"Error parsing weather response: {e}")
        return None

# =========================
# WIND DIRECTION
# =========================
def get_wind_direction(deg):
    if deg == "-" or deg is None:
        return "-"
    
    try:
        deg = float(deg)
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return directions[int((deg + 22.5) / 45) % 8]
    except (ValueError, TypeError):
        logger.warning(f"Invalid wind direction value: {deg}")
        return "-"

# =========================
# BUILD EMAIL
# =========================
def build_message():
    message = "🌏 Weekly AQI & Weather Report\n\n"

    for loc in locations:
        aqi = get_aqi_data(loc["lat"], loc["lon"])
        weather = get_weather_data(loc["lat"], loc["lon"])

        # ALWAYS show something, never skip
        temp = weather["temp"] if weather else "-"
        wind_speed = weather["wind_speed"] if weather else "-"
        wind_deg = weather.get("wind_deg") if weather else None
        wind_dir = get_wind_direction(wind_deg)

        message += f"""
📍 {loc['name']}

🧭 AQI: {aqi['aqi']} ({aqi['aqi_text']})

🌤 Temperature: {temp} °C
🌬 Wind: {wind_speed} m/s
🧭 Direction: {wind_dir}

🔬 Pollutants:
- PM2.5: {aqi['pm2_5']}
- PM10: {aqi['pm10']}
- NO₂: {aqi['no2']}
- O₃: {aqi['o3']}

------------------------
"""
    
    return message

# =========================
# SEND EMAIL
# =========================
def send_email(message):
    try:
        msg = MIMEText(message)
        msg["Subject"] = "🌏 Weekly AQI & Weather Report (Laguna)"
        msg["From"] = SENDER
        msg["To"] = ", ".join(RECEIVERS)

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        
        logger.info("Email sent successfully!")
        return True
    
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP Authentication failed. Check EMAIL_USER and EMAIL_PASS.")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email: {e}")
        return False

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        message = build_message()
        send_email(message)
    except Exception as e:
        logger.error(f"Fatal error: {e}")

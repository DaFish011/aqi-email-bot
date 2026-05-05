import requests
import smtplib
from email.mime.text import MIMEText
import os

# =========================
# ENV VARIABLES
# =========================
API_KEY = os.getenv("API_KEY")

SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")

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
        data = requests.get(url, timeout=10).json()

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

    except:
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

        data = requests.get(url, timeout=10).json()
        w = data.get("current_weather")

        if not w:
            return None

        return {
            "temp": w.get("temperature"),
            "wind_speed": w.get("windspeed"),
            "wind_deg": w.get("winddirection")
        }

    except:
        return None

# =========================
# WIND DIRECTION
# =========================
def get_wind_direction(deg):
    if deg == "-" or deg is None:
        return "-"
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((deg + 22.5) / 45) % 8]

# =========================
# BUILD EMAIL
# =========================
message = "🌏 Weekly AQI & Weather Report\n\n"

for loc in locations:
    aqi = get_aqi_data(loc["lat"], loc["lon"])
    weather = get_weather_data(loc["lat"], loc["lon"])

    # ALWAYS show something, never skip

    temp = weather["temp"] if weather else "-"
    wind_speed = weather["wind_speed"] if weather else "-"
    wind_dir = get_wind_direction(weather["wind_deg"]) if weather else "-"

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

# =========================
# SEND EMAIL
# =========================
msg = MIMEText(message)
msg["Subject"] = "🌏 Weekly AQI & Weather Report (Laguna)"
msg["From"] = SENDER
msg["To"] = ", ".join(RECEIVERS)

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(SENDER, PASSWORD)
    server.sendmail(SENDER, RECEIVERS, msg.as_string())

print("Email sent successfully!")

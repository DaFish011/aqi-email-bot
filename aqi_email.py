import requests
import smtplib
from email.mime.text import MIMEText
import os

# =========================
# ENV VARIABLES
# =========================
API_KEY = os.getenv("API_KEY")  # OpenWeather AQI key

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
# AQI INTERPRETATION
# =========================
def interpret_aqi(aqi):
    return {
        1: "Air is clean. Safe for outdoor activities.",
        2: "Acceptable air quality.",
        3: "Moderate pollution. Limit exposure.",
        4: "Poor air quality. Avoid outdoor exercise.",
        5: "Very unhealthy air. Stay indoors."
    }.get(aqi, "No data")

# =========================
# OPENWEATHER AQI
# =========================
def get_aqi_data(lat, lon):
    url = (
        f"https://api.openweathermap.org/data/2.5/air_pollution"
        f"?lat={lat}&lon={lon}&appid={API_KEY}"
    )

    data = requests.get(url).json()

    if "list" not in data:
        print("AQI ERROR:", data)
        return None

    m = data["list"][0]["main"]
    c = data["list"][0]["components"]

    return {
        "aqi": m["aqi"],
        "aqi_text": aqi_map.get(m["aqi"], "Unknown"),
        "pm2_5": c.get("pm2_5"),
        "pm10": c.get("pm10"),
        "no2": c.get("no2"),
        "o3": c.get("o3"),
    }

# =========================
# OPEN-METEO WEATHER
# =========================
def get_weather_data(lat, lon):
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current_weather=true"
    )

    data = requests.get(url).json()

    if "current_weather" not in data:
        print("WEATHER ERROR:", data)
        return None

    w = data["current_weather"]

    return {
        "temp": w["temperature"],
        "wind_speed": w["windspeed"],
        "wind_deg": w["winddirection"]
    }

# =========================
# WIND DIRECTION
# =========================
def get_wind_direction(deg):
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return directions[int((deg + 22.5) / 45) % 8]

# =========================
# BUILD EMAIL
# =========================
message = "🌏 Weekly AQI & Weather Report\n\n"

for loc in locations:
    aqi = get_aqi_data(loc["lat"], loc["lon"])
    weather = get_weather_data(loc["lat"], loc["lon"])

    if not aqi or not weather:
        continue

    message += f"""
📍 {loc['name']}

🧭 AQI: {aqi['aqi']} ({aqi['aqi_text']})
💡 {interpret_aqi(aqi['aqi'])}

🌤 Temperature: {weather['temp']} °C
🌬 Wind: {weather['wind_speed']} m/s
🧭 Direction: {get_wind_direction(weather['wind_deg'])}

🔬 Pollutants:
- PM2.5: {aqi['pm2_5']} μg/m³
- PM10: {aqi['pm10']} μg/m³
- NO₂: {aqi['no2']} μg/m³
- O₃: {aqi['o3']} μg/m³

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

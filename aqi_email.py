import requests
import smtplib
from email.mime.text import MIMEText
import os

# =========================
# 🔐 ENV VARIABLES
# =========================
API_KEY = os.getenv("API_KEY")

SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")

RECEIVERS = [
    "verdegan011@gmail.com",
    "kroderno011@gmail.com"
]

# =========================
# Locations
# =========================
locations = [
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
]

# =========================
# AQI meaning
# =========================
aqi_map = {
    1: "Good 🟢",
    2: "Fair 🟡",
    3: "Moderate 🟠",
    4: "Poor 🔴",
    5: "Very Poor 🟣"
}

# =========================
# SAFE NUMBER CONVERTER (IMPORTANT FIX)
# =========================
def safe_float(value):
    try:
        return float(value)
    except:
        return None

# =========================
# AQI INTERPRETATION
# =========================
def interpret_aqi(aqi):
    return {
        1: "Air is clean. Safe for outdoor activities.",
        2: "Air is acceptable. Sensitive people should take care.",
        3: "Moderate pollution. Limit prolonged outdoor exposure.",
        4: "Poor air quality. Avoid outdoor exercise.",
        5: "Very unhealthy. Stay indoors if possible."
    }.get(aqi, "No data")

# =========================
# TEMPERATURE INTERPRETATION (FIXED)
# =========================
def interpret_temp(temp):
    temp = safe_float(temp)
    if temp is None:
        return "No data"
    if temp < 20:
        return "Cool"
    elif temp < 28:
        return "Comfortable"
    elif temp < 33:
        return "Warm"
    else:
        return "Hot"

# =========================
# WIND INTERPRETATION (FIXED)
# =========================
def interpret_wind(speed):
    speed = safe_float(speed)
    if speed is None:
        return "No data"
    if speed < 1:
        return "Calm"
    elif speed < 3:
        return "Light breeze"
    elif speed < 6:
        return "Moderate breeze"
    elif speed < 10:
        return "Strong breeze"
    else:
        return "Very strong wind"

# =========================
# WIND DIRECTION (FIXED SAFETY)
# =========================
def get_wind_direction(deg):
    deg = safe_float(deg)
    if deg is None:
        return "-"
    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    index = int((deg + 22.5) / 45) % 8
    return directions[index]

# =========================
# AQI FUNCTION
# =========================
def get_aqi_data(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
    response = requests.get(url)
    data = response.json()

    if "list" not in data:
        print("AQI API ERROR:", data)
        return None

    main = data["list"][0]["main"]
    comp = data["list"][0]["components"]

    return {
        "aqi": main["aqi"],
        "aqi_text": aqi_map.get(main["aqi"], "Unknown"),
        "pm2_5": comp.get("pm2_5", 0),
        "pm10": comp.get("pm10", 0),
        "co": comp.get("co", 0),
        "no2": comp.get("no2", 0),
        "o3": comp.get("o3", 0),
        "so2": comp.get("so2", 0),
    }

# =========================
# WEATHER FUNCTION
# =========================
def get_weather_data(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={API_KEY}&units=metric"
    
    response = requests.get(url)
    data = response.json()

    if "main" not in data:
        print("WEATHER API ERROR:", data)
        return None

    return {
        "temp": data["main"]["temp"],
        "humidity": data["main"]["humidity"],
        "wind_speed": data["wind"]["speed"],
        "wind_deg": data["wind"]["deg"],
        "description": data["weather"][0]["description"]
    }

# =========================
# BUILD EMAIL
# =========================
message = "🌏 Weekly Air Quality & Weather Report\n\n"

for loc in locations:
    aqi = get_aqi_data(loc["lat"], loc["lon"])
    weather = get_weather_data(loc["lat"], loc["lon"])

    if not aqi or not weather:
        continue

    aqi_text = interpret_aqi(aqi["aqi"])
    temp_text = interpret_temp(weather["temp"])
    wind_text = interpret_wind(weather["wind_speed"])
    wind_dir = get_wind_direction(weather["wind_deg"])

    message += f"""
📍 {loc['name']}

🧭 AQI: {aqi['aqi']} ({aqi['aqi_text']})
💡 {aqi_text}

🌤 Weather: {weather['description']}
🌡 Temperature: {weather['temp']} °C ({temp_text})
💧 Humidity: {weather['humidity']}%
🌬 Wind: {weather['wind_speed']} m/s ({wind_text}, {wind_dir})

🔬 Pollutants:
- PM2.5: {aqi['pm2_5']} μg/m³
- PM10: {aqi['pm10']} μg/m³
- CO: {aqi['co']} μg/m³
- NO₂: {aqi['no2']} μg/m³
- O₃: {aqi['o3']} μg/m³
- SO₂: {aqi['so2']} μg/m³

------------------------
"""

# =========================
# SEND EMAIL
# =========================
msg = MIMEText(message)
msg["Subject"] = "🌏 Weekly AQI & Weather Report (Biñan & Calamba)"
msg["From"] = SENDER
msg["To"] = ", ".join(RECEIVERS)

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(SENDER, PASSWORD)
    server.sendmail(SENDER, RECEIVERS, msg.as_string())

print("Email sent successfully!")

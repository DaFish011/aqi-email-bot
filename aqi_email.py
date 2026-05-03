import requests
import smtplib
from email.mime.text import MIMEText
import os

# =========================
# 🔐 ENV VARIABLES (GitHub Secrets)
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

# AQI meaning
aqi_map = {
    1: "Good 🟢",
    2: "Fair 🟡",
    3: "Moderate 🟠",
    4: "Poor 🔴",
    5: "Very Poor 🟣"
}

def get_aqi_data(lat, lon):
    url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
    response = requests.get(url)
    data = response.json()

    if "list" not in data:
        print("API ERROR:", data)
        return {
            "aqi": "N/A",
            "aqi_text": "Error",
            "pm2_5": "-",
            "pm10": "-",
            "co": "-",
            "no2": "-",
            "o3": "-",
            "so2": "-"
        }

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
# Build email
# =========================
message = "🌏 Weekly Air Quality Report\n\n"

for loc in locations:
    data = get_aqi_data(loc["lat"], loc["lon"])

    message += f"""
📍 {loc['name']}

🧭 AQI: {data['aqi']} ({data['aqi_text']})

🔬 Pollutants:
- PM2.5: {data['pm2_5']} μg/m³
- PM10: {data['pm10']} μg/m³
- CO: {data['co']} μg/m³
- NO₂: {data['no2']} μg/m³
- O₃: {data['o3']} μg/m³
- SO₂: {data['so2']} μg/m³

------------------------
"""

# =========================
# Send email
# =========================
msg = MIMEText(message)
msg["Subject"] = "🌏 Weekly AQI Report (Biñan & Calamba)"
msg["From"] = SENDER
msg["To"] = ", ".join(RECEIVERS)

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(SENDER, PASSWORD)
    server.sendmail(SENDER, RECEIVERS, msg.as_string())

print("Email sent successfully!")

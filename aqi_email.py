import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
from requests.exceptions import RequestException

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Validate required env vars
if not all([API_KEY, SENDER, PASSWORD]):
    logger.error("Missing required environment variables: API_KEY, EMAIL_USER, or EMAIL_PASS")
    exit(1)

# =========================
# LOCATIONS
# =========================
locations = [
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
]

# =========================
# AQI LABELS & COLORS
# =========================
aqi_map = {
    1: {"label": "Good", "color": "#43a047", "advice": "Air quality is satisfactory."},
    2: {"label": "Fair", "color": "#fbc02d", "advice": "Air quality is acceptable."},
    3: {"label": "Moderate", "color": "#fb8c00", "advice": "Sensitive groups should limit outdoor activity."},
    4: {"label": "Poor", "color": "#e53935", "advice": "Everyone should reduce prolonged outdoor activity."},
    5: {"label": "Very Poor", "color": "#6a1b9a", "advice": "Avoid outdoor activity. Wear N95 masks if necessary."}
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
            "pm2_5": c.get("pm2_5"),
            "pm10": c.get("pm10"),
            "no2": c.get("no2"),
            "o3": c.get("o3"),
        }

    except RequestException as e:
        logger.error(f"API request failed for AQI data: {e}")
        return None
    except KeyError as e:
        logger.error(f"Unexpected API response structure: {e}")
        return None

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
            return None

        return {
            "temp": w.get("temperature"),
            "wind_speed": w.get("windspeed"),
            "wind_deg": w.get("winddirection")
        }

    except RequestException as e:
        logger.error(f"API request failed for weather data: {e}")
        return None

# =========================
# WIND DIRECTION
# =========================
def get_wind_direction(deg):
    if deg is None or deg == "-":
        return "-"
    try:
        directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return directions[int((float(deg) + 22.5) / 45) % 8]
    except (ValueError, TypeError):
        return "-"

# =========================
# BUILD HTML EMAIL
# =========================
def build_html_email():
    html_content = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f5f5f5; }
            .container { max-width: 600px; margin: 20px auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }
            .header h1 { margin: 0; font-size: 28px; }
            .header p { margin: 5px 0 0 0; font-size: 14px; opacity: 0.9; }
            .location-card { margin: 20px; padding: 20px; border-left: 4px solid #667eea; background-color: #f9f9f9; border-radius: 4px; }
            .location-name { font-size: 18px; font-weight: bold; color: #333; margin-bottom: 10px; }
            .aqi-box { display: inline-block; padding: 15px 20px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 15px; }
            .aqi-value { font-size: 32px; }
            .aqi-label { font-size: 16px; }
            .aqi-advice { margin-top: 10px; padding: 10px; background-color: #f0f0f0; border-radius: 4px; font-size: 13px; color: #555; }
            .weather-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin: 15px 0; }
            .weather-item { background-color: #f0f0f0; padding: 10px; border-radius: 4px; text-align: center; }
            .weather-item-label { font-size: 12px; color: #777; }
            .weather-item-value { font-size: 18px; font-weight: bold; color: #333; }
            .pollutants-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
            .pollutants-table th { background-color: #667eea; color: white; padding: 10px; text-align: left; font-size: 13px; }
            .pollutants-table td { padding: 10px; border-bottom: 1px solid #e0e0e0; font-size: 13px; }
            .pollutants-table tr:nth-child(even) { background-color: #f9f9f9; }
            .footer { background-color: #f5f5f5; padding: 15px; text-align: center; border-radius: 0 0 8px 8px; font-size: 11px; color: #999; }
            .divider { height: 1px; background-color: #e0e0e0; margin: 20px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌍 Air Quality Report</h1>
                <p>Weekly AQI & Weather Summary</p>
            </div>
    """

    for loc in locations:
        aqi_data = get_aqi_data(loc["lat"], loc["lon"])
        weather_data = get_weather_data(loc["lat"], loc["lon"])

        if not aqi_data:
            logger.warning(f"No AQI data for {loc['name']}")
            continue

        aqi_value = aqi_data.get("aqi", 0)
        aqi_info = aqi_map.get(aqi_value, aqi_map[3])  # Default to moderate
        
        temp = weather_data.get("temp", "-") if weather_data else "-"
        wind_speed = weather_data.get("wind_speed", "-") if weather_data else "-"
        wind_deg = weather_data.get("wind_deg") if weather_data else None
        wind_dir = get_wind_direction(wind_deg)

        pm2_5 = aqi_data.get("pm2_5", "-")
        pm10 = aqi_data.get("pm10", "-")
        no2 = aqi_data.get("no2", "-")
        o3 = aqi_data.get("o3", "-")

        html_content += f"""
            <div class="location-card">
                <div class="location-name">📍 {loc['name']}</div>
                
                <div class="aqi-box" style="background-color: {aqi_info['color']};">
                    <div class="aqi-value">{aqi_value}</div>
                    <div class="aqi-label">{aqi_info['label']}</div>
                </div>
                
                <div class="aqi-advice">
                    💡 {aqi_info['advice']}
                </div>
                
                <div class="weather-grid">
                    <div class="weather-item">
                        <div class="weather-item-label">Temperature</div>
                        <div class="weather-item-value">{temp}°C</div>
                    </div>
                    <div class="weather-item">
                        <div class="weather-item-label">Wind Speed</div>
                        <div class="weather-item-value">{wind_speed} m/s</div>
                    </div>
                    <div class="weather-item">
                        <div class="weather-item-label">Direction</div>
                        <div class="weather-item-value">{wind_dir}</div>
                    </div>
                </div>
                
                <table class="pollutants-table">
                    <tr>
                        <th>Pollutant</th>
                        <th>Level</th>
                    </tr>
                    <tr>
                        <td>PM2.5</td>
                        <td>{pm2_5}</td>
                    </tr>
                    <tr>
                        <td>PM10</td>
                        <td>{pm10}</td>
                    </tr>
                    <tr>
                        <td>NO₂</td>
                        <td>{no2}</td>
                    </tr>
                    <tr>
                        <td>O₃</td>
                        <td>{o3}</td>
                    </tr>
                </table>
            </div>
            <div class="divider"></div>
        """

    html_content += """
            <div class="footer">
                <p>Data sources: OpenWeatherMap API, Open-Meteo API</p>
                <p>This is an automated report. Please do not reply to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

    return html_content

# =========================
# SEND EMAIL
# =========================
def send_email():
    try:
        html_email = build_html_email()
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🌍 Weekly AQI & Weather Report (Laguna)"
        msg["From"] = SENDER
        msg["To"] = ", ".join(RECEIVERS)
        
        # Attach HTML
        msg.attach(MIMEText(html_email, "html"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        
        logger.info("Email sent successfully!")

    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed. Check EMAIL_USER and EMAIL_PASS.")
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error occurred: {e}")
    except Exception as e:
        logger.error(f"Unexpected error while sending email: {e}")

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    send_email()

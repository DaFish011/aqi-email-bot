import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import math
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
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

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
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
]

# Taal Volcano coordinates
TAAL_LAT = 14.3568
TAAL_LON = 121.0064

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
# BEARING CALCULATION (FOR TAAL)
# =========================
def get_bearing(lat1, lon1, lat2, lon2):
    """Calculate bearing from point 1 to point 2"""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360

def bearing_to_direction(bearing):
    """Convert bearing (0-360) to compass direction"""
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return directions[round(bearing / 22.5) % 16]

def is_wind_towards_taal(wind_deg, bearing_to_taal):
    """Check if wind is blowing towards Taal (within 90 degree cone)"""
    diff = abs(wind_deg - bearing_to_taal)
    if diff > 180:
        diff = 360 - diff
    return diff < 90

# =========================
# FETCH TAAL NEWS
# =========================
def get_taal_news():
    """Fetch recent Taal Volcano news from NewsAPI"""
    if not NEWS_API_KEY:
        logger.warning("NEWS_API_KEY not set. Skipping news fetch.")
        return []
    
    try:
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": "Taal Volcano eruption OR Taal activity OR Taal alert",
            "sortBy": "publishedAt",
            "language": "en",
            "apiKey": NEWS_API_KEY,
            "pageSize": 10
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        articles = data.get("articles", [])
        
        # Filter out irrelevant articles
        filtered = []
        exclude_keywords = ["pypi", "mcp", "data-mcp", "government data", "philippine", "software"]
        
        for article in articles:
            title = article.get("title", "").lower()
            description = article.get("description", "").lower() if article.get("description") else ""
            
            # Skip if contains exclude keywords
            if any(keyword in title or keyword in description for keyword in exclude_keywords):
                continue
            
            # Keep if it mentions Taal volcano specifically
            if "taal" in title or "taal" in description:
                filtered.append(article)
        
        return filtered[:5]
        
    except RequestException as e:
        logger.error(f"Failed to fetch Taal news: {e}")
        return []

# =========================
# BUILD HTML EMAIL
# =========================
def build_html_email():
    html_content = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f5f5f5; }
            .container { max-width: 1000px; margin: 20px auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }
            .header h1 { margin: 0; font-size: 28px; }
            .header p { margin: 5px 0 0 0; font-size: 14px; opacity: 0.9; }
            .location-card { padding: 20px; border-left: 4px solid #667eea; background-color: #f9f9f9; border-radius: 4px; }
            .location-name { font-size: 18px; font-weight: bold; color: #333; margin-bottom: 15px; }
            .aqi-box { display: block; padding: 15px 20px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 15px; text-align: center; }
            .aqi-value { font-size: 36px; line-height: 1; }
            .aqi-label { font-size: 16px; margin-top: 5px; }
            .aqi-pm { font-size: 12px; margin-top: 5px; opacity: 0.9; }
            .aqi-advice { margin-top: 10px; padding: 10px; background-color: #f0f0f0; border-radius: 4px; font-size: 13px; color: #555; }
            .weather-grid { display: table; width: 100%; margin: 15px 0; border-collapse: collapse; }
            .weather-cell { display: table-cell; width: 33.33%; background-color: #f0f0f0; padding: 10px; text-align: center; border: 1px solid white; }
            .weather-item-label { font-size: 12px; color: #777; }
            .weather-item-value { font-size: 18px; font-weight: bold; color: #333; }
            .pollutants-table { width: 100%; border-collapse: collapse; margin: 15px 0; }
            .pollutants-table th { background-color: #667eea; color: white; padding: 10px; text-align: left; font-size: 13px; }
            .pollutants-table td { padding: 10px; border-bottom: 1px solid #e0e0e0; font-size: 13px; }
            .pollutants-table tr:nth-child(even) { background-color: #f9f9f9; }
            .footer { background-color: #f5f5f5; padding: 15px; text-align: center; border-radius: 0 0 8px 8px; font-size: 11px; color: #999; }
            .divider { height: 1px; background-color: #e0e0e0; margin: 20px; }
            .news-section { margin: 20px; padding: 20px; background-color: #fff3e0; border-left: 4px solid #ff6f00; border-radius: 4px; }
            .news-title { color: #ff6f00; margin-top: 0; margin-bottom: 15px; }
            .news-article { margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #ffe0b2; }
            .news-article:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
            .news-article a { color: #ff6f00; text-decoration: none; font-weight: bold; }
            .news-article a:hover { text-decoration: underline; }
            .news-source { font-size: 12px; color: #999; }
            .news-desc { font-size: 13px; color: #333; margin: 5px 0 0 0; }
            .taal-info { background-color: #e3f2fd; padding: 10px; border-radius: 4px; margin-bottom: 15px; font-size: 13px; color: #1565c0; }
            .alert-card { border-left-color: #d32f2f !important; background-color: #ffebee !important; }
            .alert-message { color: #d32f2f; font-weight: bold; margin-bottom: 10px; }
            .locations-row { width: 100%; border-collapse: collapse; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌍 Air Quality Report</h1>
                <p>Weekly AQI & Weather Summary</p>
            </div>
            <table class="locations-row" cellpadding="0" cellspacing="0">
            <tr>
    """

    location_cards = []
    
    for loc in locations:
        aqi_data = get_aqi_data(loc["lat"], loc["lon"])
        weather_data = get_weather_data(loc["lat"], loc["lon"])

        if not aqi_data:
            logger.warning(f"No AQI data for {loc['name']}")
            continue

        # Get AQI level (1-5 scale)
        aqi_level = aqi_data.get("aqi", 0)
        aqi_info = aqi_map.get(aqi_level, aqi_map[3])
        
        # Convert PM2.5 to 0-500 scale
        pm2_5 = aqi_data.get("pm2_5", 0)
        aqi_numeric = min(int(pm2_5 * 4.16), 500) if pm2_5 else 0
        
        temp = weather_data.get("temp", "-") if weather_data else "-"
        wind_speed = weather_data.get("wind_speed", "-") if weather_data else "-"
        wind_deg = weather_data.get("wind_deg") if weather_data else None
        wind_dir = get_wind_direction(wind_deg)

        pm10 = aqi_data.get("pm10", "-")
        no2 = aqi_data.get("no2", "-")
        o3 = aqi_data.get("o3", "-")

        # Calculate bearing to Taal
        bearing_to_taal = get_bearing(loc["lat"], loc["lon"], TAAL_LAT, TAAL_LON)
        direction_to_taal = bearing_to_direction(bearing_to_taal)

        # Check if wind is towards Taal
        wind_towards_taal = is_wind_towards_taal(wind_deg, bearing_to_taal) if wind_deg else False
        taal_indicator = "TOWARDS" if wind_towards_taal else "AWAY FROM"

        # Determine alert styling
        is_alert = aqi_level >= 4
        alert_class = "alert-card" if is_alert else ""
        alert_border_color = "#d32f2f" if is_alert else "#667eea"
        alert_message = "<div class='alert-message'>⚠️ ALERT: Air quality is poor or very poor</div>" if is_alert else ""

        card_html = f"""
                <td style="width: 50%; padding: 10px; vertical-align: top;">
                <div class="location-card {alert_class}" style="border-left-color: {alert_border_color}; margin: 0;">
                    <div class="location-name">📍 {loc['name']}</div>
                    
                    {alert_message}
                    
                    <div class="aqi-box" style="background-color: {aqi_info['color']};">
                        <div class="aqi-value">{aqi_level}</div>
                        <div class="aqi-label">{aqi_info['label']}</div>
                        <div class="aqi-pm">PM2.5: {aqi_numeric}/500</div>
                    </div>
                    
                    <div class="aqi-advice">
                        💡 <strong>{aqi_info['label']}:</strong> {aqi_info['advice']}
                    </div>
                    
                    <div class="taal-info">
                        🌋 Wind direction: {wind_dir}. Air from your location is moving <strong>{taal_indicator} Taal</strong>
                    </div>
                    
                    <table class="weather-grid">
                    <tr>
                        <td class="weather-cell">
                            <div class="weather-item-label">Temperature</div>
                            <div class="weather-item-value">{temp}°C</div>
                        </td>
                        <td class="weather-cell">
                            <div class="weather-item-label">Wind Speed</div>
                            <div class="weather-item-value">{wind_speed} m/s</div>
                        </td>
                        <td class="weather-cell">
                            <div class="weather-item-label">Direction</div>
                            <div class="weather-item-value">{wind_dir}</div>
                        </td>
                    </tr>
                    </table>
                    
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
                </td>
        """
        location_cards.append(card_html)

    # Add all location cards
    for card in location_cards:
        html_content += card
    
    html_content += """
            </tr>
            </table>
    """

    # Add Taal News Section
    news_articles = get_taal_news()
    
    if news_articles:
        html_content += """
        <div class="news-section">
            <h3 class="news-title">🔔 Recent Taal Volcano News</h3>
        """
        for article in news_articles:
            title = article.get("title", "No title")
            description = article.get("description", "No description")
            url = article.get("url", "#")
            source = article.get("source", {}).get("name", "Unknown")
            
            html_content += f"""
            <div class="news-article">
                <a href="{url}" target="_blank">{title}</a><br>
                <span class="news-source">{source}</span><br>
                <p class="news-desc">{description}</p>
            </div>
            """
        
        html_content += """
        </div>
        """
    else:
        html_content += """
        <div style="margin: 20px; padding: 20px; background-color: #f5f5f5; border-left: 4px solid #999; border-radius: 4px;">
            <p style="color: #999; margin: 0;">ℹ️ No recent Taal activity reported</p>
        </div>
        """

    html_content += """
            <div class="footer">
                <p>Data sources: OpenWeatherMap API, Open-Meteo API, NewsAPI</p>
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

import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import math
import time
import json
import html
import urllib.parse
from datetime import datetime, timedelta
from requests.exceptions import RequestException

# =========================
# LOGGING
# =========================
log_file = os.path.expanduser("~/aqi_bot.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info("="*50)
logger.info("AQI Bot Started")
logger.info("="*50)

# =========================
# ENV VARIABLES
# =========================
API_KEY = os.getenv("API_KEY")
SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
RECEIVERS_STR = os.getenv("RECEIVERS")
if not all([API_KEY, SENDER, PASSWORD, RECEIVERS_STR]):
    logger.error("Missing required environment variables: API_KEY, EMAIL_USER, EMAIL_PASS, or RECEIVERS")
    exit(1)
# Parse comma-separated email addresses from RECEIVERS environment variable
RECEIVERS = [email.strip() for email in RECEIVERS_STR.split(",")]

# =========================
# LOCATIONS
# =========================
locations = [
    {"name": "Calamba, Laguna", "lat": 14.2118711, "lon": 121.0887077},
    {"name": "Biñan, Laguna", "lat": 14.2372, "lon": 121.0963},
]
TAAL_LAT = 14.3568
TAAL_LON = 121.0064
PH_OFFSET = timedelta(hours=8)

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

def get_aqi_level(aqi_value):
    """Map AQI value to level (1-5) for color coding"""
    if aqi_value <= 50:
        return 1
    elif aqi_value <= 100:
        return 2
    elif aqi_value <= 150:
        return 3
    elif aqi_value <= 200:
        return 4
    else:
        return 5

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
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360

def bearing_to_direction(bearing):
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                  "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return directions[round(bearing / 22.5) % 16]

def is_wind_towards_taal(wind_deg, bearing_to_taal):
    diff = abs(wind_deg - bearing_to_taal)
    if diff > 180:
        diff = 360 - diff
    return diff < 90

# =========================
# AIR QUALITY & ENVIRONMENT NEWS (NewsAPI)
# =========================
NEWS_EXCLUDE_KEYWORDS = [
    "pypi", "mcp", "data-mcp", "software", "stock market", "crypto",
    "football", "basketball", "celebrity", "k-pop", "recipe", "horoscope",
]

# Focus on Laguna region (Calamba, Biñan) and immediate vicinity
NEWS_SEARCH_QUERIES = [
    '(Calamba OR Biñan OR Binan) AND ("air quality" OR pollution OR haze OR smog OR "air pollution")',
    '(Laguna province) AND ("air quality" OR pollution OR haze OR smog OR ashfall)',
    '(Taal OR "Taal Volcano") AND (eruption OR alert OR ash OR activity OR PHIVOLCS)',
    '(Laguna OR Calamba OR Biñan) AND (wildfire OR "forest fire" OR smoke OR "open burning")',
]


def _article_text(article):
    title = article.get("title") or ""
    desc = article.get("description") or ""
    return f"{title} {desc}".lower()


def _should_exclude_article(article):
    text = _article_text(article)
    return any(kw in text for kw in NEWS_EXCLUDE_KEYWORDS)


def _is_within_week(article_date_str):
    """
    Check if article was published within the past week (Sunday to Saturday).
    Since email is sent Saturday at 8 AM, we look back 7 days.
    """
    try:
        # Parse ISO format date from NewsAPI (e.g., "2024-05-18T10:30:00Z")
        article_date = datetime.fromisoformat(article_date_str.replace("Z", "+00:00"))
        # Convert to PH timezone
        article_date_ph = article_date + PH_OFFSET
        # Current time in PH timezone (Saturday 8 AM)
        now_ph = datetime.utcnow() + PH_OFFSET
        # One week ago
        one_week_ago = now_ph - timedelta(days=7)
        return article_date_ph >= one_week_ago
    except (ValueError, TypeError):
        logger.warning(f"Could not parse article date: {article_date_str}")
        return False


def _score_and_tag_article(article):
    """Score relevance for Laguna AQI context; attach display tags."""
    text = _article_text(article)
    score = 0
    tags = []

    if any(
        t in text
        for t in (
            "taal", "phivolcs", "volcanic ash", "volcano", "eruption",
            "ashfall", "ash fall", "volcanic activity",
        )
    ):
        score += 5
        tags.append("🌋 Volcano / Taal")

    if any(
        t in text
        for t in ("air quality", "aqi", "pollution", "pm2.5", "pm10", "smog", "particulate")
    ):
        score += 4
        tags.append("🌫️ Air quality")

    if any(t in text for t in ("haze", "vog", "smoke", "wildfire", "forest fire", "open burning")):
        score += 3
        tags.append("🔥 Smoke / haze")

    if any(
        t in text
        for t in ("laguna", "calamba", "biñan", "binan")
    ):
        score += 2
        tags.append("📍 Laguna / Calamba / Biñan")

    return score, tags


def get_air_quality_news(max_articles=6):
    """
    Headlines specific to Calamba, Biñan, and Laguna region that may explain AQI changes.
    Only includes articles from the past week (Sunday to Saturday).
    """
    if not NEWS_API_KEY:
        logger.warning("NEWS_API_KEY not set. Skipping news fetch.")
        return []

    url = "https://newsapi.org/v2/everything"
    seen_urls = set()
    scored = []

    for query in NEWS_SEARCH_QUERIES:
        try:
            params = {
                "q": query,
                "sortBy": "publishedAt",
                "language": "en",
                "apiKey": NEWS_API_KEY,
                "pageSize": 10,
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            articles = response.json().get("articles", [])
        except RequestException as e:
            logger.error(f"News API request failed for query '{query[:40]}...': {e}")
            continue

        for article in articles:
            link = article.get("url")
            if not link or link in seen_urls:
                continue
            if _should_exclude_article(article):
                continue
            # Check if article is within the past week
            if not _is_within_week(article.get("publishedAt", "")):
                continue
            relevance, tags = _score_and_tag_article(article)
            if relevance < 2:
                continue
            seen_urls.add(link)
            article["_news_score"] = relevance
            article["_news_tags"] = tags
            scored.append(article)

    scored.sort(key=lambda a: a["_news_score"], reverse=True)
    return scored[:max_articles]


def _has_elevated_aqi(fetched_aqi):
    """True if any location is Moderate+ (OpenWeather 1–5) or EPA AQI > 100."""
    for data in fetched_aqi.values():
        if not data:
            continue
        if data.get("aqi", 0) >= 3:
            return True
        pm25 = data.get("pm2_5")
        if pm25 is not None and pm25_to_aqi(pm25) > 100:
            return True
    return False

# =========================
# AQI HISTORY (OPENWEATHER)
# =========================
def get_aqi_history(lat, lon, days=30):
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
    except RequestException as e:
        logger.error(f"Failed to fetch AQI history: {e}")
        return []

def compute_daily_data(history_list, current_pm25):
    """
    Past days  → daily average AQI (EPA formula from PM2.5).
    Today      → live current AQI value passed in as current_pm25.
    Skips today's readings from history to avoid mixing live + historical.
    """
    today_key = (datetime.utcnow() + PH_OFFSET).strftime("%Y-%m-%d")
    daily = {}
    for entry in history_list:
        dt_utc = datetime.utcfromtimestamp(entry["dt"])
        dt_ph = dt_utc + PH_OFFSET
        day_key = dt_ph.strftime("%Y-%m-%d")
        if day_key == today_key:
            continue  # today handled separately via live API
        pm2_5 = entry.get("components", {}).get("pm2_5") or 0
        aqi_val = pm25_to_aqi(pm2_5)
        if day_key not in daily:
            daily[day_key] = {"label": dt_ph.strftime("%b %d"), "readings": []}
        daily[day_key]["readings"].append(aqi_val)
    sorted_keys = sorted(daily.keys())
    values = [round(sum(daily[k]["readings"]) / len(daily[k]["readings"])) for k in sorted_keys]
    # Append today's live value
    if current_pm25 is not None:
        today_aqi = pm25_to_aqi(current_pm25)
        values.append(today_aqi)
    return values

# =========================
# BUILD BAR CHART URL (TWO SEPARATE CHARTS)
# =========================
def build_bar_chart_url_single(location_name, values):
    """
    Build a single bar chart showing 30-day AQI history for one location with:
    - Color-coded bars by AQI level (1-5)
    - AQI values displayed above bars for values > 100 (tilted upward)
    - Light threshold line at 100
    - Auto-scaled y-axis
    - Saturday labels on x-axis
    """
    
    if not values or len(values) == 0:
        logger.warning(f"No values provided for {location_name} chart")
        return None
    
    try:
        logger.info(f"Building chart for {location_name} with {len(values)} data points")
        
        # Calculate y-axis max with 15% buffer
        all_valid = [v for v in values if v is not None]
        logger.info(f"Valid values for {location_name}: {len(all_valid)}")
        
        max_y = max(all_valid) if all_valid else 100
        y_max = max_y + math.ceil(max_y * 0.15)
        logger.info(f"{location_name} - Max Y: {max_y}, Y-Axis Max: {y_max}")

        # Create day labels (1-30)
        days = [str(i) for i in range(1, len(values) + 1)]
        logger.info(f"{location_name} - Days: {len(days)}")

        # Create colors list
        colors = []
        for v in values:
            if v is not None:
                color = aqi_map[get_aqi_level(v)]["color"]
            else:
                color = "#ccc"
            colors.append(color)
        logger.info(f"{location_name} - Colors generated: {len(colors)}")

        # Create chart config - SIMPLIFIED
        chart_config = {
            "type": "bar",
            "data": {
                "labels": days,
                "datasets": [
                    {
                        "label": location_name,
                        "data": values,
                        "backgroundColor": colors,
                        "borderWidth": 0
                    }
                ]
            },
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "scales": {
                    "x": {
                        "grid": {"display": False}
                    },
                    "y": {
                        "min": 0,
                        "max": y_max
                    }
                }
            }
        }

        logger.info(f"{location_name} - Chart config created")

        # Encode and generate URL
        json_str = json.dumps(chart_config)
        logger.info(f"{location_name} - JSON size before encoding: {len(json_str)} chars")
        
        encoded = urllib.parse.quote(json_str)
        logger.info(f"{location_name} - JSON size after encoding: {len(encoded)} chars")
        
        chart_url = f"https://quickchart.io/chart?w=860&h=320&c={encoded}"

        url_length = len(chart_url)
        logger.info(f"Chart URL for {location_name} generated ({url_length} chars, limit: 2000)")
        
        if url_length > 2000:
            logger.error(f"Chart URL for {location_name} EXCEEDS LIMIT ({url_length} chars)")
            return None
        
        logger.info(f"Chart URL for {location_name} is VALID and will be included")
        return chart_url
    except Exception as e:
        logger.error(f"Failed to generate chart URL for {location_name}: {e}", exc_info=True)
        return None

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
            .news-title { color: #ff6f00; margin-top: 0; margin-bottom: 8px; }
            .news-subtitle { font-size: 13px; color: #666; margin: 0 0 12px 0; line-height: 1.4; }
            .news-intro { font-size: 13px; color: #c62828; font-weight: bold; margin: 0 0 12px 0; }
            .news-tags { font-size: 11px; color: #e65100; font-weight: 600; }
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
            .trend-section { margin: 20px; padding: 20px; border-left: 4px solid #667eea; background-color: #f9f9f9; border-radius: 4px; }
            .trend-title { font-size: 16px; font-weight: bold; color: #333; margin: 0 0 4px 0; }
            .trend-subtitle { font-size: 12px; color: #888; margin: 0 0 15px 0; }
            .trend-img { width: 100%; max-width: 860px; border-radius: 6px; border: 1px solid #e0e0e0; display: block; background-color: #fafafa; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌍 Air Quality Report</h1>
                <p>Weekly AQI & Weather Summary</p>
            </div>
            <table class="locations-row" cellpadding="0" cellspacing="20">
            <tr>
    """
    location_cards = []
    fetched_aqi = {}
    for loc in locations:
        aqi_data = get_aqi_data(loc["lat"], loc["lon"])
        weather_data = get_weather_data(loc["lat"], loc["lon"])
        # Store for reuse in the trend chart
        fetched_aqi[loc["name"]] = aqi_data
        if not aqi_data:
            logger.warning(f"No AQI data for {loc['name']}")
            continue
        aqi_level = aqi_data.get("aqi", 0)
        aqi_info = aqi_map.get(aqi_level, aqi_map[3])
        pm2_5 = aqi_data.get("pm2_5", 0)
        aqi_numeric = pm25_to_aqi(pm2_5)
        temp = weather_data.get("temp", "-") if weather_data else "-"
        wind_speed = weather_data.get("wind_speed", "-") if weather_data else "-"
        wind_deg = weather_data.get("wind_deg") if weather_data else None
        wind_dir = get_wind_direction(wind_deg)
        pm10 = aqi_data.get("pm10", "-")
        no2 = aqi_data.get("no2", "-")
        o3 = aqi_data.get("o3", "-")
        bearing_to_taal = get_bearing(loc["lat"], loc["lon"], TAAL_LAT, TAAL_LON)
        wind_towards_taal = is_wind_towards_taal(wind_deg, bearing_to_taal) if wind_deg else False
        
        # User-friendly wind direction messaging
        if wind_towards_taal:
            wind_message = f"🌋 Wind is blowing <strong>away from Taal</strong> (low volcanic impact expected)"
        else:
            wind_message = f"🌋 Wind is blowing <strong>towards you from Taal</strong> (could carry volcanic emissions)"
        
        is_alert = aqi_level >= 4
        alert_class = "alert-card" if is_alert else ""
        alert_border_color = "#d32f2f" if is_alert else "#667eea"
        alert_message = "<div class='alert-message'>⚠️ ALERT: Air quality is poor or very poor</div>" if is_alert else ""
        card_html = f"""
                <td style="width: 50%; padding: 20px; vertical-align: top;">
                <div class="location-card {alert_class}" style="border-left-color: {alert_border_color}; margin: 0;">
                    <div class="location-name">📍 {html.escape(loc['name'])}</div>
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
                        {wind_message}
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
                        <tr><th>Pollutant</th><th>Level</th></tr>
                        <tr><td>PM2.5</td><td>{pm2_5}</td></tr>
                        <tr><td>PM10</td><td>{pm10}</td></tr>
                        <tr><td>NO₂</td><td>{no2}</td></tr>
                        <tr><td>O₃</td><td>{o3}</td></tr>
                    </table>
                </div>
                </td>
        """
        location_cards.append(card_html)
    for card in location_cards:
        html_content += card
    html_content += """
            </tr>
            </table>
    """
    # =========================
    # 30-DAY BAR CHARTS (ONE FOR EACH LOCATION)
    # =========================
    logger.info("Fetching 30-day AQI history...")
    cal = locations[0]
    bin_ = locations[1]
    cal_aqi_now = fetched_aqi.get(cal["name"])
    bin_aqi_now = fetched_aqi.get(bin_["name"])
    cal_history = get_aqi_history(cal["lat"], cal["lon"])
    bin_history = get_aqi_history(bin_["lat"], bin_["lon"])
    cal_pm25_today = cal_aqi_now.get("pm2_5") if cal_aqi_now else None
    bin_pm25_today = bin_aqi_now.get("pm2_5") if bin_aqi_now else None
    cal_values = compute_daily_data(cal_history, cal_pm25_today)
    bin_values = compute_daily_data(bin_history, bin_pm25_today)
    
    if cal_values and bin_values:
        logger.info(f"Calamba: {len(cal_values)} days, Biñan: {len(bin_values)} days")
        
        # Generate Calamba chart
        cal_chart_url = build_bar_chart_url_single("Calamba", cal_values)
        
        # Generate Binan chart
        bin_chart_url = build_bar_chart_url_single("Biñan", bin_values)
        
        # Add both charts to email
        if cal_chart_url:
            html_content += f"""
            <div class="divider"></div>
            <div class="trend-section">
                <p class="trend-title">📊 Calamba - 30-Day AQI History</p>
                <p class="trend-subtitle">Daily AQI readings · Color-coded by severity level</p>
                <img src="{cal_chart_url}" alt="Calamba 30-day AQI bar chart" class="trend-img" />
            </div>
            """
        else:
            logger.warning("Failed to generate Calamba chart URL")
        
        if bin_chart_url:
            html_content += f"""
            <div class="trend-section">
                <p class="trend-title">📊 Biñan - 30-Day AQI History</p>
                <p class="trend-subtitle">Daily AQI readings · Color-coded by severity level</p>
                <img src="{bin_chart_url}" alt="Biñan 30-day AQI bar chart" class="trend-img" />
            </div>
            """
        else:
            logger.warning("Failed to generate Biñan chart URL")
    else:
        logger.warning("No AQI history data for charts")
    
    # =========================
    # AIR QUALITY NEWS SECTION
    # =========================
    news_articles = get_air_quality_news()
    elevated_aqi = _has_elevated_aqi(fetched_aqi)
    if news_articles:
        html_content += """
        <div class="divider"></div>
        <div class="news-section">
            <h3 class="news-title">📰 This Week's Air Quality & Environment Headlines</h3>
            <p class="news-subtitle">News from the Laguna region and Taal area that may affect air quality — volcano activity, regional pollution, and smoke events.</p>
        """
        if elevated_aqi:
            html_content += """
            <p class="news-intro">⚠️ Elevated AQI detected in at least one location. Related headlines below.</p>
        """
        for article in news_articles:
            title = html.escape(article.get("title") or "No title")
            description = html.escape(article.get("description") or "No description")
            url = html.escape(article.get("url") or "#", quote=True)
            source = html.escape((article.get("source") or {}).get("name", "Unknown"))
            tags = article.get("_news_tags", [])
            tags_line = html.escape(" · ".join(tags)) if tags else ""
            tags_html = f'<span class="news-tags">{tags_line}</span><br>' if tags_line else ""
            html_content += f"""
            <div class="news-article">
                {tags_html}
                <a href="{url}" target="_blank">{title}</a><br>
                <span class="news-source">{source}</span><br>
                <p class="news-desc">{description}</p>
            </div>
            """
        html_content += "</div>"
    else:
        empty_msg = (
            "ℹ️ No matching headlines this week. AQI drivers can include weather patterns, traffic, fires, or Taal activity."
            if elevated_aqi
            else "ℹ️ No recent air-quality or volcano-related headlines found for the Laguna region this week."
        )
        html_content += f"""
        <div style="margin: 20px; padding: 20px; background-color: #f5f5f5; border-left: 4px solid #999; border-radius: 4px;">
            <p style="color: #999; margin: 0;">{html.escape(empty_msg)}</p>
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
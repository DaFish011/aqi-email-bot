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
from datetime import datetime, timedelta
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
# FETCH TAAL NEWS
# =========================
def get_taal_news():
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
        filtered = []
        exclude_keywords = ["pypi", "mcp", "data-mcp", "government data", "philippine", "software"]
        for article in articles:
            title = article.get("title", "").lower()
            description = article.get("description", "").lower() if article.get("description") else ""
            if any(keyword in title or keyword in description for keyword in exclude_keywords):
                continue
            if "taal" in title or "taal" in description:
                filtered.append(article)
        return filtered[:5]
    except RequestException as e:
        logger.error(f"Failed to fetch Taal news: {e}")
        return []

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
    today_key = (datetime.utcnow() + PH_OFFSET).strftime("%Y-%m-%d")
    daily = {}
    for entry in history_list:
        dt_utc = datetime.utcfromtimestamp(entry["dt"])
        dt_ph = dt_utc + PH_OFFSET
        day_key = dt_ph.strftime("%Y-%m-%d")
        if day_key == today_key:
            continue
        pm2_5 = entry.get("components", {}).get("pm2_5") or 0
        aqi_val = pm25_to_aqi(pm2_5)
        if day_key not in daily:
            daily[day_key] = {"label": dt_ph.strftime("%b %d"), "readings": []}
        daily[day_key]["readings"].append(aqi_val)
    sorted_keys = sorted(daily.keys())
    labels = [daily[k]["label"] for k in sorted_keys]
    values = [round(sum(daily[k]["readings"]) / len(daily[k]["readings"])) for k in sorted_keys]
    if current_pm25 is not None:
        today_aqi = pm25_to_aqi(current_pm25)
        today_label = (datetime.utcnow() + PH_OFFSET).strftime("%b %d")
        labels.append(today_label)
        values.append(today_aqi)
    return labels, values

def merge_labels(cal_labels, cal_values, bin_labels, bin_values):
    all_labels = sorted(
        set(cal_labels) | set(bin_labels),
        key=lambda d: datetime.strptime(d, "%b %d").replace(year=datetime.utcnow().year)
    )
    cal_map = dict(zip(cal_labels, cal_values))
    bin_map = dict(zip(bin_labels, bin_values))
    merged_cal = [cal_map.get(l) for l in all_labels]
    merged_bin = [bin_map.get(l) for l in all_labels]
    return all_labels, merged_cal, merged_bin

# =========================
# BUILD TREND CHART URL
# =========================
def build_trend_chart_url(labels, cal_values, bin_values):
    MUTED_CAL = "#5c6bc0"
    MUTED_BIN = "#ffb74d"
    ALERT_RED = "#e53935"
    BG_FILL_CAL = "rgba(92,107,192,0.10)"
    BG_FILL_BIN = "rgba(255,183,77,0.08)"

    def point_colors(values, base):
        return [ALERT_RED if (v is not None and v > 100) else base for v in (values or [])]

    def base_point_radii(values):
        return [6 if (v is not None and v > 100) else 3 for v in (values or [])]

    def emphasize_last(radii):
        if not radii:
            return radii
        r = radii[:]
        r[-1] = max(r[-1], 9)  # emphasize last point
        return r

    cal_radii = emphasize_last(base_point_radii(cal_values))
    bin_radii = emphasize_last(base_point_radii(bin_values))

    def helper_values_for_labels(values):
        return [v if (v is not None and v > 100) else None for v in (values or [])]

    cal_helper = helper_values_for_labels(cal_values)
    bin_helper = helper_values_for_labels(bin_values)

    all_valid = [v for v in (cal_values or []) + (bin_values or []) if v is not None]
    max_y = max(max(all_valid) if all_valid else 100, 100) + 30
    max_y = min(max_y, 300)

    chart_config = {
        "type": "line",
        "data": {
            "labels": labels or [],
            "datasets": [
                {
                    "label": "Calamba",
                    "data": cal_values or [],
                    "borderColor": MUTED_CAL,
                    "backgroundColor": BG_FILL_CAL,
                    "borderWidth": 1.2,
                    "fill": True,
                    "pointBackgroundColor": point_colors(cal_values, MUTED_CAL),
                    "pointRadius": cal_radii,
                    "tension": 0.24
                },
                {
                    "label": "Biñan",
                    "data": bin_values or [],
                    "borderColor": MUTED_BIN,
                    "backgroundColor": BG_FILL_BIN,
                    "borderWidth": 1.2,
                    "fill": True,
                    "pointBackgroundColor": point_colors(bin_values, MUTED_BIN),
                    "pointRadius": bin_radii,
                    "tension": 0.24
                },
                # helper dataset for Calamba labels (>100 only)
                {
                    "label": None,
                    "data": cal_helper,
                    "borderWidth": 0,
                    "pointBackgroundColor": [ALERT_RED if v else "rgba(0,0,0,0)" for v in cal_helper],
                    "pointRadius": [8 if v else 0 for v in cal_helper],
                    "showLine": False,
                    "fill": False,
                    "spanGaps": True,
                    "datalabels": {
                        "display": True,
                        "color": "#fff",
                        "backgroundColor": ALERT_RED,
                        "borderRadius": 4,
                        "font": {"size": 11, "weight": "600"},
                        "padding": 6,
                        "align": "top",
                        "anchor": "end"
                    },
                    "hidden": True,
                    "showInLegend": False
                },
                # helper dataset for Biñan labels (>100 only)
                {
                    "label": None,
                    "data": bin_helper,
                    "borderWidth": 0,
                    "pointBackgroundColor": [ALERT_RED if v else "rgba(0,0,0,0)" for v in bin_helper],
                    "pointRadius": [8 if v else 0 for v in bin_helper],
                    "showLine": False,
                    "fill": False,
                    "spanGaps": True,
                    "datalabels": {
                        "display": True,
                        "color": "#fff",
                        "backgroundColor": ALERT_RED,
                        "borderRadius": 4,
                        "font": {"size": 11, "weight": "600"},
                        "padding": 6,
                        "align": "top",
                        "anchor": "end"
                    },
                    "hidden": True,
                    "showInLegend": False
                }
            ]
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "layout": {"padding": {"top": 12, "bottom": 18, "left": 10, "right": 10}},
            "plugins": {
                "title": {
                    "display": True,
                    "text": "30 days: daily average AQI",
                    "color": "#333",
                    "font": {"size": 14, "weight": "600"},
                    "padding": {"top": 6, "bottom": 6}
                },
                "legend": {
                    "position": "bottom",   # legend at bottom
                    "align": "center",
                    "labels": {
                        "usePointStyle": True,
                        "pointStyle": "circle",
                        "boxWidth": 10,
                        "padding": 8,
                        "color": "#444",
                        "font": {"size": 12}
                    }
                },
                "annotation": {
                    "annotations": {
                        "threshold_line": {
                            "type": "line",
                            "yMin": 100,
                            "yMax": 100,
                            "borderColor": ALERT_RED,
                            "borderDash": [6, 4],
                            "borderWidth": 1.6,
                            "opacity": 0.95
                        }
                    }
                }
            },
            "scales": {
                "x": {
                    "type": "category",
                    "grid": {"display": False},
                    "ticks": {"color": "#666", "maxRotation": 0, "autoSkip": True}
                },
                "y": {
                    "min": 0,
                    "max": max_y,
                    "grid": {"color": "#f3f3f3"},
                    "ticks": {
                        "color": "#666",
                        "callback": "function(value){ return value === 100 ? '100 — Threshold' : value; }"
                    },
                    "title": {"display": True, "text": "AQI", "color": "#666", "font": {"size": 11}}
                }
            },
            "elements": {
                "line": {"borderWidth": 1.2},
                "point": {"hoverRadius": 8}
            }
        }
    }

    encoded = urllib.parse.quote(json.dumps(chart_config))
    return f"https://quickchart.io/chart?w=860&h=380&c={encoded}"



# =========================
# BUILD HTML EMAIL
# =========================
def build_html_email():
    html_content = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f5f5f5; }
            .container { max-width: 1000px; margin: 20px auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.06); }
            .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 18px; text-align: center; border-radius: 8px 8px 0 0; }
            .header h1 { margin: 0; font-size: 24px; }
            .header p { margin: 6px 0 0 0; font-size: 13px; opacity: 0.95; }
            .location-card { padding: 18px; border-left: 4px solid #667eea; background-color: #fbfbfb; border-radius: 4px; }
            .location-name { font-size: 16px; font-weight: bold; color: #333; margin-bottom: 12px; }
            .aqi-box { display: block; padding: 12px 16px; border-radius: 8px; color: white; font-weight: bold; margin-bottom: 12px; text-align: center; }
            .aqi-value { font-size: 32px; line-height: 1; }
            .aqi-label { font-size: 14px; margin-top: 4px; }
            .aqi-pm { font-size: 12px; margin-top: 4px; opacity: 0.9; }
            .aqi-advice { margin-top: 10px; padding: 10px; background-color: #f0f0f0; border-radius: 4px; font-size: 13px; color: #555; }
            .weather-grid { display: table; width: 100%; margin: 12px 0; border-collapse: collapse; }
            .weather-cell { display: table-cell; width: 33.33%; background-color: #f7f7f7; padding: 8px; text-align: center; border: 1px solid white; }
            .weather-item-label { font-size: 12px; color: #777; }
            .weather-item-value { font-size: 16px; font-weight: bold; color: #333; }
            .pollutants-table { width: 100%; border-collapse: collapse; margin: 12px 0; }
            .pollutants-table th { background-color: #667eea; color: white; padding: 8px; text-align: left; font-size: 13px; }
            .pollutants-table td { padding: 8px; border-bottom: 1px solid #eaeaea; font-size: 13px; }
            .pollutants-table tr:nth-child(even) { background-color: #fafafa; }
            .footer { background-color: #f5f5f5; padding: 12px; text-align: center; border-radius: 0 0 8px 8px; font-size: 11px; color: #999; }
            .divider { height: 1px; background-color: #e0e0e0; margin: 16px; }
            .news-section { margin: 16px; padding: 16px; background-color: #fff3e0; border-left: 4px solid #ff6f00; border-radius: 4px; }
            .news-title { color: #ff6f00; margin-top: 0; margin-bottom: 12px; }
            .news-article { margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid #ffe0b2; }
            .news-article:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
            .news-article a { color: #ff6f00; text-decoration: none; font-weight: bold; }
            .news-source { font-size: 12px; color: #999; }
            .news-desc { font-size: 13px; color: #333; margin: 6px 0 0 0; }
            .taal-info { background-color: #e3f2fd; padding: 8px; border-radius: 4px; margin-bottom: 12px; font-size: 13px; color: #1565c0; }
            .alert-card { border-left-color: #d32f2f !important; background-color: #fff5f5 !important; }
            .alert-message { color: #d32f2f; font-weight: bold; margin-bottom: 8px; }
            .trend-section { margin: 16px; padding: 16px; border-left: 4px solid #667eea; background-color: #fbfbfb; border-radius: 4px; }
            .trend-title { font-size: 15px; font-weight: bold; color: #333; margin: 0 0 6px 0; }
            .trend-subtitle { font-size: 12px; color: #888; margin: 0 0 12px 0; }
            .trend-img { width: 100%; max-width: 860px; border-radius: 6px; border: 1px solid #eaeaea; display: block; background-color: #fff; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌍 Air Quality Report</h1>
                <p>Weekly AQI & Weather Summary</p>
            </div>
            <table style="width:100%; border-collapse: collapse; padding: 16px;">
            <tr>
    """
    location_cards = []
    fetched_aqi = {}
    for loc in locations:
        aqi_data = get_aqi_data(loc["lat"], loc["lon"])
        weather_data = get_weather_data(loc["lat"], loc["lon"])
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
        taal_indicator = "TOWARDS" if wind_towards_taal else "AWAY FROM"
        is_alert = aqi_level >= 4
        alert_class = "alert-card" if is_alert else ""
        alert_border_color = "#d32f2f" if is_alert else "#667eea"
        alert_message = "<div class='alert-message'>⚠️ ALERT: Air quality is poor or very poor</div>" if is_alert else ""
        card_html = f"""
                <td style="width: 50%; padding: 12px; vertical-align: top;">
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
    # 30-DAY AQI TREND CHART
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
    cal_labels, cal_values = compute_daily_data(cal_history, cal_pm25_today)
    bin_labels, bin_values = compute_daily_data(bin_history, bin_pm25_today)
    if cal_labels or bin_labels:
        labels, cal_values, bin_values = merge_labels(
            cal_labels, cal_values, bin_labels, bin_values
        )
        chart_url = build_trend_chart_url(labels, cal_values, bin_values)
        html_content += f"""
        <div class="divider"></div>
        <div class="trend-section">
            <p class="trend-title">📈 30-Day AQI Trend</p>
            <p class="trend-subtitle">Past 30 days: daily average AQI · Today: live reading · Red labels = above threshold (100)</p>
            <img src="{chart_url}" alt="30-day AQI trend" class="trend-img" />
        </div>
        """
    else:
        logger.warning("No AQI history data for trend chart.")
    # =========================
    # TAAL NEWS SECTION
    # =========================
    news_articles = get_taal_news()
    if news_articles:
        html_content += """
        <div class="divider"></div>
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
        html_content += "</div>"
    else:
        html_content += """
        <div style="margin: 16px; padding: 12px; background-color: #f5f5f5; border-left: 4px solid #999; border-radius: 4px;">
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

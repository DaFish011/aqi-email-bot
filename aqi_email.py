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
# BUILD TREND CHART URL (REDESIGNED)
# =========================
def build_trend_chart_url(labels, cal_values, bin_values):
    """
    Redesigned chart config:
    - Legend at bottom (only Calamba & Biñan)
    - Dashed red threshold line at y=100
    - Invisible helper datasets (no 'label' key) used only for datalabels above threshold
    - Muted indigo/amber palette, subtle fills, thin lines
    - Emphasize last point
    - skipNull on helpers to avoid ghost points
    """
    MUTED_CAL = "#3f51b5"      # slightly deeper indigo
    MUTED_BIN = "#ffb74d"      # warm amber
    ALERT_RED = "#e53935"
    BG_FILL_CAL = "rgba(63,81,181,0.10)"
    BG_FILL_BIN = "rgba(255,183,77,0.08)"

    def point_colors(values, base):
        return [ALERT_RED if (v is not None and v > 100) else base for v in (values or [])]

    def base_point_radii(values):
        return [6 if (v is not None and v > 100) else 3 for v in (values or [])]

    def emphasize_last(radii):
        if not radii:
            return radii
        r = radii[:]
        r[-1] = max(r[-1], 9)
        return r

    cal_radii = emphasize_last(base_point_radii(cal_values))
    bin_radii = emphasize_last(base_point_radii(bin_values))

    def helper_values_for_labels(values):
        # return value only if >100, else None (so skipNull works)
        return [v if (v is not None and v > 100) else None for v in (values or [])]

    cal_helper = helper_values_for_labels(cal_values)
    bin_helper = helper_values_for_labels(bin_values)

    # compute y-axis max with headroom
    all_valid = [v for v in (cal_values or []) + (bin_values or []) if v is not None]
    max_y = max(max(all_valid) if all_valid else 100, 100) + 30
    max_y = min(max_y, 400)

    chart_config = {
        "type": "line",
        "data": {
            "labels": labels or [],
            "datasets": [
                # Calamba main series
                {
                    "label": "Calamba",
                    "data": cal_values or [],
                    "borderColor": MUTED_CAL,
                    "backgroundColor": BG_FILL_CAL,
                    "borderWidth": 1.25,
                    "fill": True,
                    "pointBackgroundColor": point_colors(cal_values, MUTED_CAL),
                    "pointRadius": cal_radii,
                    "tension": 0.22,
                    "order": 1
                },
                # Biñan main series
                {
                    "label": "Biñan",
                    "data": bin_values or [],
                    "borderColor": MUTED_BIN,
                    "backgroundColor": BG_FILL_BIN,
                    "borderWidth": 1.25,
                    "fill": True,
                    "pointBackgroundColor": point_colors(bin_values, MUTED_BIN),
                    "pointRadius": bin_radii,
                    "tension": 0.22,
                    "order": 1
                },
                # helper dataset for Calamba labels (>100 only)
                {
                    # intentionally no "label" key here — prevents legend entry
                    "data": cal_helper,
                    "borderWidth": 0,
                    "pointBackgroundColor": [ALERT_RED if v else "rgba(0,0,0,0)" for v in cal_helper],
                    "pointRadius": [8 if v else 0 for v in cal_helper],
                    "showLine": False,
                    "fill": False,
                    "spanGaps": True,
                    "skipNull": True,
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
                    "showInLegend": False,
                    "order": 2
                },
                # helper dataset for Biñan labels (>100 only)
                {
                    # intentionally no "label" key here — prevents legend entry
                    "data": bin_helper,
                    "borderWidth": 0,
                    "pointBackgroundColor": [ALERT_RED if v else "rgba(0,0,0,0)" for v in bin_helper],
                    "pointRadius": [8 if v else 0 for v in bin_helper],
                    "showLine": False,
                    "fill": False,
                    "spanGaps": True,
                    "skipNull": True,
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
                    "showInLegend": False,
                    "order": 2
                }
            ]
        },
        "options": {
            "responsive": True,
            "maintainAspectRatio": False,
            "layout": {
                "padding": {"top": 14, "bottom": 22, "left": 12, "right": 12}
            },
            "plugins": {
                "title": {
                    "display": True,
                    "text": "30-day daily average AQI — Calamba vs Biñan",
                    "color": "#222",
                    "font": {"size": 14, "weight": "600"},
                    "padding": {"top": 6, "bottom": 8}
                },
                "legend": {
                    "position": "bottom",
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
                            "opacity": 0.95,
                            "label": {
                                "enabled": True,
                                "content": "AQI 100 threshold",
                                "position": "end",
                                "backgroundColor": "rgba(229,57,53,0.95)",
                                "color": "#fff",
                                "font": {"size": 10, "weight": "600"},
                                "padding": 6
                            }
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
                "line": {"borderWidth": 1.25},
                "point": {"hoverRadius": 8}
            }
        }
    }

    encoded = urllib.parse.quote(json.dumps(chart_config))
    return f"https://quickchart.io/chart?w=920&h=420&c={encoded}"

# =========================
# BUILD HTML EMAIL
# =========================
def build_html_email():
    html_content = """
    <html>
    <head>
        <style>
            body { font-family: Arial, sans-serif; background-color: #f5f5f5; }
            .container { max-width: 1000px; margin: 20px auto; background-color: white; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.06); }
            .header { background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); color: white; padding: 18px; text-align: center; border-radius: 8px 8px 0 0; }
            .header h1 { margin: 0; font-size: 22px; }
            .header p { margin: 6px 0 0 0; font-size: 13px; opacity: 0.95; }
            .grid { display: flex; gap: 12px; padding: 16px; }
            .card { flex: 1; padding: 14px; border-radius: 6px; background: #fbfbff; border-left: 4px solid #4f46e5; }
            .location-name { font-size: 15px; font-weight: 700; color: #222; margin-bottom: 8px; }
            .aqi-box { padding: 10px 12px; border-radius: 8px; color: white; font-weight: 700; text-align: center; margin-bottom: 10px; }
            .aqi-value { font-size: 28px; }
            .aqi-label { font-size: 13px; margin-top: 4px; }
            .small { font-size: 12px; color: #666; }
            .trend-section { margin: 16px; padding: 16px; border-left: 4px solid #4f46e5; background-color: #fbfbff; border-radius: 6px; }
            .trend-img { width: 100%; max-width: 920px; border-radius: 6px; border: 1px solid #eaeaea; display: block; background-color: #fff; }
            .footer { background-color: #f5f5f5; padding: 12px; text-align: center; border-radius: 0 0 8px 8px; font-size: 11px; color: #999; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🌍 Air Quality Report</h1>
                <p>30-day trend · Live readings · Threshold labels for AQI &gt; 100</p>
            </div>
            <div class="grid">
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
        alert_border_color = "#d32f2f" if is_alert else "#4f46e5"
        alert_message = "<div style='color:#d32f2f;font-weight:700;margin-bottom:8px;'>⚠️ ALERT: Poor air quality</div>" if is_alert else ""
        card_html = f"""
            <div class="card" style="border-left-color:{alert_border_color};">
                <div class="location-name">📍 {loc['name']}</div>
                {alert_message}
                <div class="aqi-box" style="background-color: {aqi_info['color']};">
                    <div class="aqi-value">{aqi_level}</div>
                    <div class="aqi-label">{aqi_info['label']}</div>
                </div>
                <div class="small">PM2.5 (µg/m³): {pm2_5} · AQI (est): {aqi_numeric}</div>
                <div style="margin-top:10px;" class="small">Temp: {temp}°C · Wind: {wind_speed} m/s ({wind_dir})</div>
                <div style="margin-top:8px;" class="small">Pollutants: PM10 {pm10} · NO₂ {no2} · O₃ {o3}</div>
                <div style="margin-top:8px;" class="small">Air relative to Taal: {taal_indicator}</div>
            </div>
        """
        location_cards.append(card_html)
    for card in location_cards:
        html_content += card
    html_content += """
            </div>
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
        <div class="trend-section">
            <p style="margin:0;font-weight:700;color:#222;">📈 30-Day AQI Trend</p>
            <p style="margin:6px 0 12px 0;color:#666;font-size:13px;">Daily average AQI · Red labels show values above threshold (100)</p>
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
        <div style="margin:16px;padding:12px;background:#fff7ed;border-left:4px solid #ff8a00;border-radius:6px;">
            <p style="margin:0 0 8px 0;font-weight:700;color:#ff6f00;">🔔 Recent Taal Volcano News</p>
        """
        for article in news_articles:
            title = article.get("title", "No title")
            description = article.get("description", "No description")
            url = article.get("url", "#")
            source = article.get("source", {}).get("name", "Unknown")
            html_content += f"""
            <div style="margin-bottom:10px;">
                <a href="{url}" style="color:#ff6f00;font-weight:700;text-decoration:none;" target="_blank">{title}</a><br>
                <span style="font-size:12px;color:#888;">{source}</span>
                <p style="margin:6px 0 0 0;color:#333;font-size:13px;">{description}</p>
            </div>
            """
        html_content += "</div>"
    else:
        html_content += """
        <div style="margin:16px;padding:12px;background:#f5f5f5;border-left:4px solid #ccc;border-radius:6px;">
            <p style="margin:0;color:#777;">ℹ️ No recent Taal activity reported</p>
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
        msg["Subject"] = "🌍 Air Quality Report — Calamba & Biñan"
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

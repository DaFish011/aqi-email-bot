import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
import math
import time
import json
import base64
import io
from datetime import datetime, timedelta
from requests.exceptions import RequestException

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =========================
# ENV VARIABLES
# =========================
API_KEY      = os.getenv("API_KEY")
SENDER       = os.getenv("EMAIL_USER")
PASSWORD     = os.getenv("EMAIL_PASS")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
RECEIVERS    = [
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
    {"name": "Biñan, Laguna",   "lat": 14.3386, "lon": 121.0807},
]
TAAL_LAT  = 14.3568
TAAL_LON  = 121.0064
PH_OFFSET = timedelta(hours=8)

# =========================
# AQI LABELS & COLORS
# =========================
aqi_map = {
    1: {"label": "Good",      "color": "#43a047", "advice": "Air quality is satisfactory."},
    2: {"label": "Fair",      "color": "#fbc02d", "advice": "Air quality is acceptable."},
    3: {"label": "Moderate",  "color": "#fb8c00", "advice": "Sensitive groups should limit outdoor activity."},
    4: {"label": "Poor",      "color": "#e53935", "advice": "Everyone should reduce prolonged outdoor activity."},
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
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        m = data["list"][0]["main"]
        c = data["list"][0]["components"]
        return {"aqi": m.get("aqi"), "pm2_5": c.get("pm2_5"),
                "pm10": c.get("pm10"), "no2": c.get("no2"), "o3": c.get("o3")}
    except (RequestException, KeyError) as e:
        logger.error(f"AQI fetch error: {e}")
        return None

# =========================
# WEATHER FUNCTION (OPEN-METEO)
# =========================
def get_weather_data(lat, lon):
    try:
        url = (f"https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}&current_weather=true")
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        w = r.json().get("current_weather")
        if not w:
            return None
        return {"temp": w.get("temperature"), "wind_speed": w.get("windspeed"),
                "wind_deg": w.get("winddirection")}
    except RequestException as e:
        logger.error(f"Weather fetch error: {e}")
        return None

# =========================
# WIND DIRECTION
# =========================
def get_wind_direction(deg):
    if deg is None or deg == "-":
        return "-"
    try:
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return dirs[int((float(deg) + 22.5) / 45) % 8]
    except (ValueError, TypeError):
        return "-"

# =========================
# BEARING / TAAL HELPERS
# =========================
def get_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

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
        params = {
            "q": "Taal Volcano eruption OR Taal activity OR Taal alert",
            "sortBy": "publishedAt", "language": "en",
            "apiKey": NEWS_API_KEY, "pageSize": 10
        }
        r = requests.get("https://newsapi.org/v2/everything", params=params, timeout=10)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        exclude = ["pypi", "mcp", "data-mcp", "government data", "philippine", "software"]
        filtered = []
        for a in articles:
            t = a.get("title", "").lower()
            d = (a.get("description") or "").lower()
            if any(k in t or k in d for k in exclude):
                continue
            if "taal" in t or "taal" in d:
                filtered.append(a)
        return filtered[:5]
    except RequestException as e:
        logger.error(f"News fetch error: {e}")
        return []

# =========================
# AQI HISTORY (OPENWEATHER)
# =========================
def get_aqi_history(lat, lon, days=30):
    end   = int(time.time())
    start = int((datetime.utcnow() - timedelta(days=days)).timestamp())
    try:
        url = (f"https://api.openweathermap.org/data/2.5/air_pollution/history"
               f"?lat={lat}&lon={lon}&start={start}&end={end}&appid={API_KEY}")
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("list", [])
    except RequestException as e:
        logger.error(f"AQI history fetch error: {e}")
        return []

def compute_daily_data(history_list, current_pm25):
    today_key = (datetime.utcnow() + PH_OFFSET).strftime("%Y-%m-%d")
    daily = {}
    for entry in history_list:
        dt_ph   = datetime.utcfromtimestamp(entry["dt"]) + PH_OFFSET
        day_key = dt_ph.strftime("%Y-%m-%d")
        if day_key == today_key:
            continue
        aqi_val = pm25_to_aqi(entry.get("components", {}).get("pm2_5") or 0)
        if day_key not in daily:
            daily[day_key] = {"label": dt_ph.strftime("%b %d"), "readings": []}
        daily[day_key]["readings"].append(aqi_val)
    sorted_keys = sorted(daily.keys())
    labels = [daily[k]["label"] for k in sorted_keys]
    values = [round(sum(daily[k]["readings"]) / len(daily[k]["readings"])) for k in sorted_keys]
    if current_pm25 is not None:
        labels.append((datetime.utcnow() + PH_OFFSET).strftime("%b %d"))
        values.append(pm25_to_aqi(current_pm25))
    return labels, values

def merge_labels(cal_labels, cal_values, bin_labels, bin_values):
    all_labels = sorted(
        set(cal_labels) | set(bin_labels),
        key=lambda d: datetime.strptime(d, "%b %d").replace(year=datetime.utcnow().year)
    )
    cal_map = dict(zip(cal_labels, cal_values))
    bin_map = dict(zip(bin_labels, bin_values))
    return all_labels, [cal_map.get(l) for l in all_labels], [bin_map.get(l) for l in all_labels]

# =========================
# BUILD CHART → BASE64 PNG
# =========================
def build_trend_chart_base64(labels, cal_values, bin_values):
    CAL_COLOR  = "#00897b"
    BIN_COLOR  = "#f57c00"
    ALERT_RED  = "#d32f2f"
    THRESHOLD  = 100

    xs = list(range(len(labels)))

    # Replace None with NaN so matplotlib gaps properly
    import numpy as np
    cal_arr = [v if v is not None else float("nan") for v in cal_values]
    bin_arr = [v if v is not None else float("nan") for v in bin_values]

    fig, ax = plt.subplots(figsize=(11, 4.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#fafafa")

    # --- Filled area ---
    ax.fill_between(xs, cal_arr, alpha=0.15, color=CAL_COLOR)
    ax.fill_between(xs, bin_arr, alpha=0.15, color=BIN_COLOR)

    # --- Lines ---
    ax.plot(xs, cal_arr, color=CAL_COLOR, linewidth=2, zorder=3)
    ax.plot(xs, bin_arr, color=BIN_COLOR, linewidth=2, zorder=3)

    # --- Points: normal vs breach ---
    for arr, base_color in [(cal_arr, CAL_COLOR), (bin_arr, BIN_COLOR)]:
        for i, v in enumerate(arr):
            if math.isnan(v):
                continue
            is_breach  = v > THRESHOLD
            is_last    = (i == len(arr) - 1)
            color      = ALERT_RED if is_breach else base_color
            size       = 70 if is_breach else (90 if is_last else 25)
            ax.scatter(i, v, color=color, s=size, zorder=5)
            # Label only breach points
            if is_breach:
                ax.annotate(
                    str(v),
                    xy=(i, v), xytext=(0, 7),
                    textcoords="offset points",
                    ha="center", va="bottom",
                    fontsize=9, fontweight="bold", color=ALERT_RED
                )

    # --- Red dashed threshold line ---
    ax.axhline(y=THRESHOLD, color=ALERT_RED, linestyle="--", linewidth=1.8, zorder=2)
    ax.text(
        0.01, THRESHOLD + 3, "⚠ Unhealthy (AQI 100)",
        transform=ax.get_yaxis_transform(),
        fontsize=9, fontweight="bold", color="white",
        bbox=dict(boxstyle="round,pad=0.3", facecolor=ALERT_RED, edgecolor="none")
    )

    # --- Axes ---
    step = max(1, len(labels) // 15)
    ax.set_xticks(xs[::step])
    ax.set_xticklabels(labels[::step], rotation=30, ha="right", fontsize=8, color="#555")
    ax.set_ylabel("Air Quality Index (AQI)", fontsize=10, color="#555")
    ax.tick_params(axis="y", labelcolor="#555", labelsize=9)
    ax.set_xlim(-0.5, len(labels) - 0.5)
    all_valid = [v for v in cal_arr + bin_arr if not math.isnan(v)]
    max_y = min(max(max(all_valid) if all_valid else 100, 100) + 40, 320)
    ax.set_ylim(0, max_y)
    ax.grid(axis="y", color="#e0e0e0", linewidth=0.8)
    ax.grid(axis="x", visible=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # --- Legend at bottom with circle markers ---
    legend_elements = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor=CAL_COLOR,
               markersize=9, label="Calamba"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=BIN_COLOR,
               markersize=9, label="Biñan"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor=ALERT_RED,
               markersize=9, label="Above threshold"),
        Line2D([0], [0], linestyle="--", color=ALERT_RED,
               linewidth=1.8, label="Unhealthy threshold (100)"),
    ]
    ax.legend(
        handles=legend_elements,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=4,
        frameon=False,
        fontsize=10
    )

    plt.title("Laguna AQI Trends – Last 30 Days", fontsize=13, fontweight="bold",
              color="#222", pad=10)
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")

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
            .news-source { font-size: 12px; color: #999; }
            .news-desc { font-size: 13px; color: #333; margin: 5px 0 0 0; }
            .taal-info { background-color: #e3f2fd; padding: 10px; border-radius: 4px; margin-bottom: 15px; font-size: 13px; color: #1565c0; }
            .alert-card { border-left-color: #d32f2f !important; background-color: #ffebee !important; }
            .alert-message { color: #d32f2f; font-weight: bold; margin-bottom: 10px; }
            .locations-row { width: 100%; border-collapse: collapse; }
            .trend-section { margin: 20px; padding: 20px; border-left: 4px solid #667eea; background-color: #f9f9f9; border-radius: 4px; }
            .trend-title { font-size: 16px; font-weight: bold; color: #333; margin: 0 0 4px 0; }
            .trend-subtitle { font-size: 12px; color: #888; margin: 0 0 15px 0; }
            .trend-img { width: 100%; max-width: 860px; border-radius: 6px; border: 1px solid #e0e0e0; display: block; }
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
        aqi_data     = get_aqi_data(loc["lat"], loc["lon"])
        weather_data = get_weather_data(loc["lat"], loc["lon"])
        fetched_aqi[loc["name"]] = aqi_data
        if not aqi_data:
            logger.warning(f"No AQI data for {loc['name']}")
            continue
        aqi_level   = aqi_data.get("aqi", 0)
        aqi_info    = aqi_map.get(aqi_level, aqi_map[3])
        pm2_5       = aqi_data.get("pm2_5", 0)
        aqi_numeric = pm25_to_aqi(pm2_5)
        temp        = weather_data.get("temp", "-")      if weather_data else "-"
        wind_speed  = weather_data.get("wind_speed", "-") if weather_data else "-"
        wind_deg    = weather_data.get("wind_deg")        if weather_data else None
        wind_dir    = get_wind_direction(wind_deg)
        pm10, no2, o3 = aqi_data.get("pm10", "-"), aqi_data.get("no2", "-"), aqi_data.get("o3", "-")
        bearing_to_taal   = get_bearing(loc["lat"], loc["lon"], TAAL_LAT, TAAL_LON)
        wind_towards_taal = is_wind_towards_taal(wind_deg, bearing_to_taal) if wind_deg else False
        taal_indicator    = "TOWARDS" if wind_towards_taal else "AWAY FROM"
        is_alert          = aqi_level >= 4
        alert_class        = "alert-card" if is_alert else ""
        alert_border_color = "#d32f2f"   if is_alert else "#667eea"
        alert_message      = "<div class='alert-message'>⚠️ ALERT: Air quality is poor or very poor</div>" if is_alert else ""
        location_cards.append(f"""
            <td style="width:50%;padding:20px;vertical-align:top;">
            <div class="location-card {alert_class}" style="border-left-color:{alert_border_color};margin:0;">
                <div class="location-name">📍 {loc['name']}</div>
                {alert_message}
                <div class="aqi-box" style="background-color:{aqi_info['color']};">
                    <div class="aqi-value">{aqi_level}</div>
                    <div class="aqi-label">{aqi_info['label']}</div>
                    <div class="aqi-pm">PM2.5: {aqi_numeric}/500</div>
                </div>
                <div class="aqi-advice">💡 <strong>{aqi_info['label']}:</strong> {aqi_info['advice']}</div>
                <div class="taal-info">🌋 Wind direction: {wind_dir}. Air from your location is moving <strong>{taal_indicator} Taal</strong></div>
                <table class="weather-grid"><tr>
                    <td class="weather-cell"><div class="weather-item-label">Temperature</div><div class="weather-item-value">{temp}°C</div></td>
                    <td class="weather-cell"><div class="weather-item-label">Wind Speed</div><div class="weather-item-value">{wind_speed} m/s</div></td>
                    <td class="weather-cell"><div class="weather-item-label">Direction</div><div class="weather-item-value">{wind_dir}</div></td>
                </tr></table>
                <table class="pollutants-table">
                    <tr><th>Pollutant</th><th>Level</th></tr>
                    <tr><td>PM2.5</td><td>{pm2_5}</td></tr>
                    <tr><td>PM10</td><td>{pm10}</td></tr>
                    <tr><td>NO₂</td><td>{no2}</td></tr>
                    <tr><td>O₃</td><td>{o3}</td></tr>
                </table>
            </div>
            </td>
        """)
    for card in location_cards:
        html_content += card
    html_content += "</tr></table>"

    # =========================
    # 30-DAY AQI TREND CHART
    # =========================
    logger.info("Fetching 30-day AQI history...")
    cal, bin_ = locations[0], locations[1]
    cal_history  = get_aqi_history(cal["lat"],  cal["lon"])
    bin_history  = get_aqi_history(bin_["lat"], bin_["lon"])
    cal_pm25_now = (fetched_aqi.get(cal["name"])  or {}).get("pm2_5")
    bin_pm25_now = (fetched_aqi.get(bin_["name"]) or {}).get("pm2_5")
    cal_labels, cal_values = compute_daily_data(cal_history, cal_pm25_now)
    bin_labels, bin_values = compute_daily_data(bin_history, bin_pm25_now)

    if cal_labels or bin_labels:
        labels, cal_values, bin_values = merge_labels(cal_labels, cal_values, bin_labels, bin_values)
        chart_b64 = build_trend_chart_base64(labels, cal_values, bin_values)
        html_content += f"""
        <div class="divider"></div>
        <div class="trend-section">
            <p class="trend-title">📈 30-Day AQI Trend</p>
            <p class="trend-subtitle">Past 30 days: daily average AQI · Today: live reading · <span style="color:#d32f2f;">●</span> Above unhealthy threshold (100)</p>
            <img src="data:image/png;base64,{chart_b64}" alt="30-day AQI trend" class="trend-img" />
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
            title  = article.get("title", "No title")
            desc   = article.get("description", "No description")
            url    = article.get("url", "#")
            source = article.get("source", {}).get("name", "Unknown")
            html_content += f"""
            <div class="news-article">
                <a href="{url}" target="_blank">{title}</a><br>
                <span class="news-source">{source}</span>
                <p class="news-desc">{desc}</p>
            </div>
            """
        html_content += "</div>"
    else:
        html_content += """
        <div style="margin:20px;padding:20px;background-color:#f5f5f5;border-left:4px solid #999;border-radius:4px;">
            <p style="color:#999;margin:0;">ℹ️ No recent Taal activity reported</p>
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
        msg["From"]    = SENDER
        msg["To"]      = ", ".join(RECEIVERS)
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

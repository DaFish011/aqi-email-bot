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
from datetime import datetime, timedelta, timezone
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
RECEIVERS_STR = os.getenv("RECEIVERS")

if not all([API_KEY, SENDER, PASSWORD, RECEIVERS_STR]):
    logger.error("Missing required environment variables.")
    exit(1)

RECEIVERS = [email.strip() for email in RECEIVERS_STR.split(",")]

# =========================
# LOCATIONS & CONSTANTS
# =========================
locations = [
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
]
TAAL_LAT = 14.0023  # Main Crater coordinates
TAAL_LON = 120.9928
PH_OFFSET = timedelta(hours=8)

aqi_map = {
    1: {"label": "Good", "color": "#43a047", "advice": "Air quality is satisfactory."},
    2: {"label": "Fair", "color": "#fbc02d", "advice": "Air quality is acceptable."},
    3: {"label": "Moderate", "color": "#fb8c00", "advice": "Sensitive groups should limit outdoor activity."},
    4: {"label": "Poor", "color": "#e53935", "advice": "Everyone should reduce prolonged outdoor activity."},
    5: {"label": "Very Poor", "color": "#6a1b9a", "advice": "Avoid outdoor activity. Wear N95 masks."}
}

# =========================
# PM2.5 → AQI (EPA Formula)
# =========================
def pm25_to_aqi(pm25):
    if pm25 is None or pm25 < 0: return 0
    breakpoints = [
        (0.0, 12.0, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
        (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300), (250.5, 500.4, 301, 500)
    ]
    for c_low, c_high, aqi_low, aqi_high in breakpoints:
        if c_low <= pm25 <= c_high:
            return round(((aqi_high - aqi_low) / (c_high - c_low)) * (pm25 - c_low) + aqi_low)
    return 500

# =========================
# GEOSPATIAL & WEATHER LOGIC
# =========================
def get_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def get_wind_direction(deg):
    if deg is None: return "N/A"
    directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    return directions[round(float(deg) / 22.5) % 16]

def analyze_taal_risk(wind_deg, bearing_to_taal):
    """Determines if wind is blowing FROM Taal toward the location."""
    if wind_deg is None: return "Unknown Risk Data", "#9e9e9e"
    # Wind direction is where it's coming FROM. If it matches the bearing TO Taal, 
    # then the wind is blowing from Taal toward the observer.
    diff = abs(wind_deg - bearing_to_taal)
    if diff > 180: diff = 360 - diff
    
    if diff < 35:
        return "Elevated Risk: Wind blowing from Taal direction", "#d32f2f"
    elif diff < 70:
        return "Moderate Risk: Airflow from Taal vicinity", "#fb8c00"
    else:
        return "Low Risk: Wind blowing away from Taal", "#43a047"

# =========================
# API DATA FETCHING
# =========================
def get_aqi_data(lat, lon):
    try:
        url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        d = r.json()["list"][0]
        return {"aqi": d["main"]["aqi"], **d["components"]}
    except Exception as e:
        logger.error(f"AQI fetch error: {e}")
        return None

def get_weather_data(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        w = r.json().get("current_weather")
        return {"temp": w["temperature"], "wind_speed": w["windspeed"], "wind_deg": w["winddirection"]}
    except Exception as e:
        logger.error(f"Weather fetch error: {e}")
        return None

def get_aqi_history(lat, lon, days=30):
    end = int(time.time())
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    try:
        url = f"https://api.openweathermap.org/data/2.5/air_pollution/history?lat={lat}&lon={lon}&start={start}&end={end}&appid={API_KEY}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("list", [])
    except Exception as e:
        logger.error(f"History fetch error: {e}")
        return []

def get_aqi_related_news():
    if not NEWS_API_KEY: return []
    try:
        lookback = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": "(Taal OR 'air quality' OR 'sulfur dioxide' OR Vog OR ashfall OR haze) AND (Laguna OR Philippines)",
            "from": lookback,
            "sortBy": "relevancy",
            "language": "en",
            "apiKey": NEWS_API_KEY,
            "pageSize": 8
        }
        r = requests.get(url, params=params, timeout=10)
        articles = r.json().get("articles", [])
        impact_keywords = ["vog", "sulfur", "so2", "pollution", "ash", "health", "mask", "haze", "smog"]
        return [a for a in articles if any(k in (a['title'] + (a['description'] or '')).lower() for k in impact_keywords)][:4]
    except Exception as e:
        logger.error(f"News error: {e}")
        return []

# =========================
# DATA MERGING & CHART
# =========================
def compute_daily_data(history_list, current_pm25):
    today_key = (datetime.now(timezone.utc) + PH_OFFSET).strftime("%Y-%m-%d")
    daily = {}
    for entry in history_list:
        dt_ph = datetime.fromtimestamp(entry["dt"], timezone.utc) + PH_OFFSET
        day_key = dt_ph.strftime("%Y-%m-%d")
        if day_key == today_key: continue
        val = pm25_to_aqi(entry.get("components", {}).get("pm2_5", 0))
        if day_key not in daily: daily[day_key] = {"label": dt_ph.strftime("%b %d"), "vals": []}
        daily[day_key]["vals"].append(val)
    
    sorted_keys = sorted(daily.keys())
    labels = [daily[k]["label"] for k in sorted_keys]
    values = [round(sum(daily[k]["vals"])/len(daily[k]["vals"])) for k in sorted_keys]
    
    if current_pm25:
        labels.append((datetime.now(timezone.utc) + PH_OFFSET).strftime("%b %d"))
        values.append(pm25_to_aqi(current_pm25))
    return labels, values

def build_trend_chart_url(labels, cal_values, bin_values):
    chart_config = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [
                {"label": "Calamba", "data": cal_values, "borderColor": "#00897b", "backgroundColor": "rgba(0,137,123,0.1)", "fill": True, "tension": 0.4},
                {"label": "Biñan", "data": bin_values, "borderColor": "#f57c00", "backgroundColor": "rgba(245,124,0,0.1)", "fill": True, "tension": 0.4}
            ]
        },
        "options": {
            "title": {"display": True, "text": "30-Day Air Quality Trend", "fontSize": 18},
            "legend": {"position": "bottom"},
            "scales": {"yAxes": [{"ticks": {"beginAtZero": True}}]},
            "chartArea": {"backgroundColor": "#f8f9fa"}
        }
    }
    encoded = urllib.parse.quote(json.dumps(chart_config))
    return f"https://quickchart.io/chart?w=800&h=400&bkg=white&c={encoded}"

# =========================
# EMAIL CONSTRUCTION
# =========================
def build_html_email():
    now_ph = (datetime.now(timezone.utc) + PH_OFFSET).strftime("%B %d, %Y | %I:%M %p")
    fetched_data = []
    
    for loc in locations:
        aqi = get_aqi_data(loc["lat"], loc["lon"])
        weather = get_weather_data(loc["lat"], loc["lon"])
        history = get_aqi_history(loc["lat"], loc["lon"])
        fetched_data.append({"loc": loc, "aqi": aqi, "weather": weather, "history": history})

    # Prepare Chart
    l1, v1 = compute_daily_data(fetched_data[0]["history"], fetched_data[0]["aqi"].get("pm2_5") if fetched_data[0]["aqi"] else None)
    l2, v2 = compute_daily_data(fetched_data[1]["history"], fetched_data[1]["aqi"].get("pm2_5") if fetched_data[1]["aqi"] else None)
    
    # Simple merge for chart labels
    all_labels = sorted(list(set(l1 + l2)), key=lambda x: datetime.strptime(x, "%b %d"))
    v1_merged = [v1[l1.index(lab)] if lab in l1 else None for lab in all_labels]
    v2_merged = [v2[l2.index(lab)] if lab in l2 else None for lab in all_labels]
    chart_url = build_trend_chart_url(all_labels, v1_merged, v2_merged)

    html = f"""
    <html>
    <body style="font-family: 'Helvetica Neue', Arial, sans-serif; background-color: #f8f9fa; padding: 20px; color: #333;">
        <div style="max-width: 850px; margin: auto; background: white; border-radius: 12px; overflow: hidden; border: 1px solid #e0e0e0; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <div style="background: #2c3e50; color: white; padding: 40px 20px; text-align: center;">
                <h1 style="margin: 0; font-size: 26px; letter-spacing: 1px;">Environmental Status Report</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.8; font-size: 14px;">{now_ph} PH Time</p>
            </div>
            
            <div style="padding: 30px; background: #fdfdfd;">
                <table width="100%" cellspacing="0" cellpadding="0">
                    <tr>
    """
    
    for item in fetched_data:
        loc, aqi, w = item["loc"], item["aqi"], item["weather"]
        if not aqi: continue
        
        info = aqi_map.get(aqi["aqi"], aqi_map[3])
        bearing = get_bearing(loc["lat"], loc["lon"], TAAL_LAT, TAAL_LON)
        risk_msg, risk_color = analyze_taal_risk(w["wind_deg"], bearing)
        
        html += f"""
                        <td width="50%" valign="top" style="padding: 10px;">
                            <div style="border: 1px solid #eee; border-radius: 10px; padding: 20px; border-top: 4px solid {info['color']};">
                                <h2 style="margin: 0 0 15px 0; font-size: 18px;">📍 {loc['name']}</h2>
                                <div style="background: {info['color']}; color: white; padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 15px;">
                                    <span style="font-size: 32px; font-weight: bold;">{aqi['aqi']}</span><br>
                                    <span style="font-size: 14px; text-transform: uppercase;">{info['label']}</span>
                                </div>
                                <p style="font-size: 13px; color: #666; margin-bottom: 20px;">{info['advice']}</p>
                                
                                <div style="background: #f8f9fa; border-radius: 6px; padding: 12px; font-size: 13px; border: 1px solid #eee;">
                                    <strong style="color: {risk_color};">Taal Analysis:</strong><br>
                                    {risk_msg}<br>
                                    <span style="color: #999; font-size: 11px;">Wind from {get_wind_direction(w['wind_deg'])} ({w['wind_deg']}°)</span>
                                </div>
                            </div>
                        </td>
        """

    html += f"""
                    </tr>
                </table>

                <div style="margin-top: 30px; text-align: center;">
                    <h3 style="font-size: 16px; color: #2c3e50; margin-bottom: 15px;">📈 30-Day Air Quality Trend</h3>
                    <img src="{chart_url}" style="width: 100%; border-radius: 8px; border: 1px solid #eee;">
                </div>
    """

    # News Section
    news = get_aqi_related_news()
    if news:
        html += '<div style="margin-top: 30px; padding: 20px; border-top: 1px dashed #ddd;"><h3>🔔 Recent Environmental Events</h3>'
        for art in news:
            html += f"""
                <div style="margin-bottom: 15px;">
                    <a href="{art['url']}" style="color: #3498db; text-decoration: none; font-weight: bold; font-size: 14px;">{art['title']}</a><br>
                    <small style="color: #999;">{art['source']['name']} | {art['publishedAt'][:10]}</small>
                </div>"""
        html += "</div>"
    
    html += """
                <div style="margin-top: 30px; padding: 20px; text-align: center; color: #bdc3c7; font-size: 11px; border-top: 1px solid #eee;">
                    Sources: OpenWeatherMap, Open-Meteo, NewsAPI. Automated Report.
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

def send_email():
    try:
        content = build_html_email()
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "🌍 Laguna Air Quality & Environmental Risk Report"
        msg["From"] = SENDER
        msg["To"] = ", ".join(RECEIVERS)
        msg.attach(MIMEText(content, "html"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        logger.info("Report successfully sent.")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")

if __name__ == "__main__":
    send_email()

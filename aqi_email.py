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

# =========================
# CONFIG & ENV
# =========================
API_KEY = os.getenv("API_KEY")
SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
RECEIVERS = [email.strip() for email in os.getenv("RECEIVERS", "").split(",")]

locations = [
    {"name": "Calamba, Laguna", "lat": 14.2117, "lon": 121.1653},
    {"name": "Biñan, Laguna", "lat": 14.3386, "lon": 121.0807},
]
TAAL_LAT, TAAL_LON = 14.0023, 120.9928
PH_OFFSET = timedelta(hours=8)

# =========================
# CORE LOGIC
# =========================
def pm25_to_aqi(pm25):
    if pm25 is None or pm25 < 0: return 0
    # EPA Standard Breakpoints
    bp = [(0, 12, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
          (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300), (250.5, 500.4, 301, 500)]
    for low, high, i_low, i_high in bp:
        if low <= pm25 <= high:
            return round(((i_high - i_low) / (high - low)) * (pm25 - low) + i_low)
    return 500

def get_taal_analysis(lat, lon, wind_deg):
    # Bearing FROM location TO Taal
    lat1, lon1, lat2, lon2 = map(math.radians, [lat, lon, TAAL_LAT, TAAL_LON])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    
    # Wind direction is where it COMES FROM. 
    # If wind_deg matches bearing, wind is blowing FROM Taal TO Location.
    diff = abs(wind_deg - bearing)
    if diff > 180: diff = 360 - diff
    
    if diff < 40:
        return "⚠️ Elevated Risk: Wind blowing FROM Taal", "#d32f2f"
    return "✅ Low Risk: Wind blowing AWAY from Taal", "#2e7d32"

# =========================
# DATA FETCHING
# =========================
def get_dashboard_data(lat, lon):
    try:
        # AQI & Pollutants
        aqi_res = requests.get(f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}").json()
        comp = aqi_res["list"][0]["components"]
        aqi_idx = aqi_res["list"][0]["main"]["aqi"]
        
        # Weather
        w_res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true").json()["current_weather"]
        
        # 30-Day History (Fixed timestamp logic)
        end = int(time.time())
        start = end - (30 * 24 * 60 * 60)
        hist_res = requests.get(f"https://api.openweathermap.org/data/2.5/air_pollution/history?lat={lat}&lon={lon}&start={start}&end={end}&appid={API_KEY}").json().get("list", [])
        
        return {"aqi": aqi_idx, "pm25": comp["pm2_5"], "pm10": comp["pm10"], "no2": comp["no2"], "o3": comp["o3"],
                "temp": w_res["temperature"], "wind": w_res["windspeed"], "deg": w_res["winddirection"], "history": hist_res}
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def get_laguna_impact_news():
    if not NEWS_API_KEY: return []
    # Search specifically for Taal eruptions/Vog or fires affecting Laguna
    q = "(Taal OR 'volcanic smog' OR 'landfill fire' OR 'forest fire') AND (Laguna OR CALABARZON OR Batangas)"
    url = f"https://newsapi.org/v2/everything?q={urllib.parse.quote(q)}&sortBy=publishedAt&apiKey={NEWS_API_KEY}&language=en"
    try:
        articles = requests.get(url).json().get("articles", [])
        return articles[:4] # Latest 4 relevant news
    except: return []

# =========================
# CHART GENERATOR (FIXED)
# =========================
def generate_chart_url(loc_data):
    # Consolidate daily averages to fix the "messed up" graph
    daily_stats = {}
    for entry in loc_data[0]["data"]["history"]:
        day = datetime.fromtimestamp(entry["dt"], timezone.utc).strftime("%m/%d")
        if day not in daily_stats: daily_stats[day] = []
        daily_stats[day].append(pm25_to_aqi(entry["components"]["pm2_5"]))
    
    labels = sorted(daily_stats.keys())[-14:] # Last 14 days for readability
    values = [round(sum(daily_stats[d])/len(daily_stats[d])) for d in labels]

    chart = {
        "type": "line",
        "data": {
            "labels": labels,
            "datasets": [{"label": loc_data[0]["name"], "data": values, "borderColor": "#667eea", "fill": False}]
        },
        "options": {"title": {"display": True, "text": "14-Day AQI Trend (PM2.5)"}, "chartArea": {"backgroundColor": "#f8f9fa"}}
    }
    return f"https://quickchart.io/chart?w=800&h=300&c={urllib.parse.quote(json.dumps(chart))}"

# =========================
# EMAIL CONSTRUCTION
# =========================
def send_report():
    all_loc_data = []
    for loc in locations:
        data = get_dashboard_data(loc["lat"], loc["lon"])
        if data: all_loc_data.append({"name": loc["name"], "lat": loc["lat"], "lon": loc["lon"], "data": data})

    aqi_styles = {
        1: ("#43a047", "Good"), 2: ("#fbc02d", "Fair"), 3: ("#fb8c00", "Moderate"),
        4: ("#e53935", "Poor"), 5: ("#6a1b9a", "Very Poor")
    }

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial; background: #f4f7f6; margin: 0; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 40px; text-align: center; border-radius: 10px 10px 0 0; }}
            .card {{ background: white; margin-bottom: 25px; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 15px rgba(0,0,0,0.1); border: 1px solid #ddd; }}
            .aqi-banner {{ color: white; padding: 20px; text-align: center; font-size: 24px; font-weight: bold; }}
            .grid {{ display: table; width: 100%; border-collapse: collapse; }}
            .col {{ display: table-cell; padding: 20px; border: 1px solid #eee; text-align: center; }}
            .pollutants {{ width: 100%; border-collapse: collapse; }}
            .pollutants td {{ padding: 10px; border-bottom: 1px solid #eee; font-size: 14px; }}
            .news-box {{ background: #fff8e1; border-left: 5px solid #ffc107; padding: 15px; margin-top: 20px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div style="max-width: 900px; margin: auto;">
            <div class="header">
                <h1 style="margin:0;">Laguna Environmental Dashboard</h1>
                <p>{(datetime.now(timezone.utc)+PH_OFFSET).strftime('%Y-%m-%d %H:%M')}</p>
            </div>
    """

    for item in all_loc_data:
        d = item["data"]
        color, label = aqi_styles.get(d["aqi"], ("#9e9e9e", "Unknown"))
        risk_msg, risk_color = get_taal_analysis(item["lat"], item["lon"], d["deg"])
        
        html += f"""
        <div class="card">
            <div style="padding:20px; font-size:20px; font-weight:bold; border-bottom:1px solid #eee;">📍 {item['name']}</div>
            <div class="aqi-banner" style="background:{color};">AQI: {d['aqi']} ({label})</div>
            <div class="grid">
                <div class="col"><strong>{d['temp']}°C</strong><br><small>Temp</small></div>
                <div class="col"><strong>{d['wind']} km/h</strong><br><small>Wind Speed</small></div>
                <div class="col"><strong>{d['pm25']}</strong><br><small>PM2.5 (μg/m³)</small></div>
            </div>
            <div style="padding:20px; background:{risk_color}10; border-top:2px solid {risk_color};">
                <strong>Taal Risk:</strong> {risk_msg}
            </div>
            <div style="padding:20px;">
                <table class="pollutants">
                    <tr><td><b>PM10:</b> {d['pm10']}</td><td><b>NO₂:</b> {d['no2']}</td><td><b>O₃:</b> {d['o3']}</td></tr>
                </table>
            </div>
        </div>
        """

    # Chart & News
    chart_url = generate_chart_url(all_loc_data)
    html += f'<div class="card" style="padding:20px; text-align:center;"><img src="{chart_url}" style="max-width:100%;"></div>'
    
    news = get_laguna_impact_news()
    if news:
        html += '<div class="news-box"><h3>🔔 Air Quality Impact Alerts</h3>'
        for a in news:
            html += f'<p><a href="{a["url"]}" style="color:#d32f2f; font-weight:bold;">{a["title"]}</a><br><small>{a["source"]["name"]}</small></p>'
        html += '</div>'

    html += "</div></body></html>"
    
    # Mailer Logic
    msg = MIMEMultipart(); msg["Subject"] = "Laguna Air Quality & Weather Report"; msg["From"] = SENDER; msg["To"] = ", ".join(RECEIVERS)
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER, PASSWORD)
        server.sendmail(SENDER, RECEIVERS, msg.as_string())

if __name__ == "__main__":
    send_report()

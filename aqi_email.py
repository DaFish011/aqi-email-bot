import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
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
    bp = [(0, 12, 0, 50), (12.1, 35.4, 51, 100), (35.5, 55.4, 101, 150),
          (55.5, 150.4, 151, 200), (150.5, 250.4, 201, 300), (250.5, 500.4, 301, 500)]
    for low, high, i_low, i_high in bp:
        if low <= pm25 <= high:
            return round(((i_high - i_low) / (high - low)) * (pm25 - low) + i_low)
    return 500

def get_taal_analysis(lat, lon, wind_deg):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat, lon, TAAL_LAT, TAAL_LON])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360
    
    diff = abs(wind_deg - bearing)
    if diff > 180: diff = 360 - diff
    
    if diff < 45: # Wind coming from Taal direction
        return "⚠️ Elevated Risk: Wind blowing FROM Taal", "#d32f2f"
    return "✅ Low Risk: Wind blowing AWAY from Taal", "#2e7d32"

# =========================
# DATA FETCHING
# =========================
def get_dashboard_data(lat, lon):
    try:
        aqi_url = f"https://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={lon}&appid={API_KEY}"
        aqi_res = requests.get(aqi_url).json()
        comp = aqi_res["list"][0]["components"]
        
        w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        w_res = requests.get(w_url).json()["current_weather"]
        
        end = int(time.time())
        start = end - (30 * 24 * 60 * 60)
        hist_url = f"https://api.openweathermap.org/data/2.5/air_pollution/history?lat={lat}&lon={lon}&start={start}&end={end}&appid={API_KEY}"
        hist_res = requests.get(hist_url).json().get("list", [])
        
        return {
            "aqi_val": pm25_to_aqi(comp["pm2_5"]),
            "pm25": comp["pm2_5"], "pm10": comp["pm10"], "no2": comp["no2"], "o3": comp["o3"],
            "temp": w_res["temperature"], "wind": w_res["windspeed"], "deg": w_res["winddirection"], 
            "history": hist_res
        }
    except Exception as e:
        print(f"Error fetching data for {lat},{lon}: {e}")
        return None

def get_laguna_impact_news():
    if not NEWS_API_KEY: return []
    q = "(Taal OR 'volcanic smog' OR 'vog' OR 'ashfall') AND (Laguna OR Batangas OR Calamba OR Biñan)"
    url = f"https://newsapi.org/v2/everything?q={urllib.parse.quote(q)}&sortBy=publishedAt&apiKey={NEWS_API_KEY}&language=en"
    try:
        articles = requests.get(url).json().get("articles", [])
        return articles[:4]
    except: return []

# =========================
# CHART GENERATOR
# =========================
def generate_chart_url(all_loc_data):
    datasets = []
    colors = ["#667eea", "#f57c00"] # Blue for Calamba, Orange for Biñan
    global_labels = []

    for i, item in enumerate(all_loc_data):
        daily_stats = {}
        for entry in item["data"]["history"]:
            day = datetime.fromtimestamp(entry["dt"], timezone.utc).strftime("%m/%d")
            if day not in daily_stats: daily_stats[day] = []
            daily_stats[day].append(pm25_to_aqi(entry["components"]["pm2_5"]))
        
        labels = sorted(daily_stats.keys())[-21:] # Last 21 days for balance
        values = [round(sum(daily_stats[d])/len(daily_stats[d])) for d in labels]
        if not global_labels: global_labels = labels

        datasets.append({
            "label": item["name"],
            "data": values,
            "borderColor": colors[i],
            "fill": False,
            "datalabels": {
                "display": True,
                "align": "top",
                "backgroundColor": "white",
                "borderRadius": 3,
                "padding": 2,
                "font": {"size": 10, "weight": "bold"},
                "color": "#d32f2f",
                # The crucial feature: only show label if AQI >= 100
                "formatter": "ctx => ctx < 100 ? '' : ctx"
            }
        })

    chart = {
        "type": "line",
        "data": {"labels": global_labels, "datasets": datasets},
        "options": {
            "title": {"display": True, "text": "Laguna AQI Trend (Last 21 Days)"},
            "plugins": {
                "datalabels": {"display": True},
                "annotation": {
                    "annotations": [{
                        "type": "line", "mode": "horizontal", "scaleID": "y",
                        "value": 100, "borderColor": "red", "borderWidth": 2, "borderDash": [5, 5]
                    }]
                }
            }
        }
    }
    return f"https://quickchart.io/chart?w=800&h=400&c={urllib.parse.quote(json.dumps(chart))}"

# =========================
# EMAIL CONSTRUCTION & SENDING
# =========================
def send_report():
    all_loc_data = []
    for loc in locations:
        data = get_dashboard_data(loc["lat"], loc["lon"])
        if data: all_loc_data.append({"name": loc["name"], "lat": loc["lat"], "lon": loc["lon"], "data": data})

    def get_aqi_theme(aqi):
        if aqi <= 50: return ("#43a047", "Good")
        if aqi <= 100: return ("#fbc02d", "Fair")
        if aqi <= 150: return ("#fb8c00", "Moderate")
        return ("#e53935", "Poor")

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f7f6; margin: 0; padding: 20px; color: #333; }}
            .container {{ max-width: 800px; margin: auto; background: white; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.08); }}
            .header {{ background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; padding: 40px 20px; text-align: center; }}
            .card {{ margin: 20px; border: 1px solid #eee; border-radius: 10px; overflow: hidden; }}
            .aqi-banner {{ color: white; padding: 15px; text-align: center; font-size: 24px; font-weight: bold; }}
            .grid {{ display: table; width: 100%; border-collapse: collapse; }}
            .col {{ display: table-cell; padding: 15px; border: 1px solid #f0f0f0; text-align: center; width: 33%; }}
            .news-box {{ background: #fff8e1; border-left: 5px solid #ffc107; padding: 20px; margin: 20px; border-radius: 5px; }}
            .footer {{ text-align: center; padding: 20px; font-size: 12px; color: #777; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin:0;">Laguna Air Quality Report</h1>
                <p style="opacity: 0.8;">{(datetime.now(timezone.utc)+PH_OFFSET).strftime('%A, %B %d, %Y | %I:%M %p')}</p>
            </div>
    """

    for item in all_loc_data:
        d = item["data"]
        color, label = get_aqi_theme(d["aqi_val"])
        risk_msg, risk_color = get_taal_analysis(item["lat"], item["lon"], d["deg"])
        
        html += f"""
        <div class="card">
            <div style="padding:15px; background:#f8f9fa; font-weight:bold; font-size:18px;">📍 {item['name']}</div>
            <div class="aqi-banner" style="background:{color};">AQI: {d['aqi_val']} — {label}</div>
            <div class="grid">
                <div class="col"><strong>{d['temp']}°C</strong><br><small>Temp</small></div>
                <div class="col"><strong>{d['wind']} km/h</strong><br><small>Wind ({d['deg']}°)</small></div>
                <div class="col"><strong>{d['pm25']}</strong><br><small>PM2.5 (μg/m³)</small></div>
            </div>
            <div style="padding:15px; background:{risk_color}10; border-top:2px solid {risk_color}; color:{risk_color}; font-weight:bold;">
                Taal Status: {risk_msg}
            </div>
        </div>
        """

    # Add Chart
    chart_url = generate_chart_url(all_loc_data)
    html += f"""
        <div style="padding:20px; text-align:center;">
            <h3 style="margin-top:0;">Environmental Trend</h3>
            <img src="{chart_url}" style="width:100%; border-radius:8px; border:1px solid #ddd;">
            <p style="font-size:11px; color:#999;">Red labels appear only for AQI levels 100 and above.</p>
        </div>
    """

    # Add News
    news = get_laguna_impact_news()
    if news:
        html += '<div class="news-box"><h3>🔔 Latest Local Alerts</h3>'
        for a in news:
            html += f'<p style="margin-bottom:15px;"><a href="{a["url"]}" style="color:#d32f2f; text-decoration:none; font-weight:bold;">{a["title"]}</a><br><small style="color:#666;">Source: {a["source"]["name"]}</small></p>'
        html += '</div>'

    html += f"""
            <div class="footer">
                This automated report is based on current PHIVOLCS observations and OpenWeather data.<br>
                Stay safe and monitor official channels for emergency updates.
            </div>
        </div>
    </body>
    </html>
    """

    # SMTP Logic
    if not all([SENDER, PASSWORD, RECEIVERS]):
        print("Missing Email Credentials. Outputting HTML to console instead.")
        print(html)
        return

    msg = MIMEMultipart()
    msg["Subject"] = f"Laguna Air Quality Alert: {(datetime.now(timezone.utc)+PH_OFFSET).strftime('%m/%d/%Y')}"
    msg["From"] = f"Laguna Weather Station <{SENDER}>"
    msg["To"] = ", ".join(RECEIVERS)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        print("Report successfully sent to all receivers.")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    send_report()

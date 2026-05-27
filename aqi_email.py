import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
import logging
import math
import html
import firebase_admin
from firebase_admin import db, credentials
from datetime import datetime, timedelta
from requests.exceptions import RequestException
import plotly.graph_objects as go

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
IQAIR_API_KEY = os.getenv("IQAIR_API_KEY")
SENDER = os.getenv("EMAIL_USER")
PASSWORD = os.getenv("EMAIL_PASS")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
RECEIVERS_STR = os.getenv("RECEIVERS")
if not all([IQAIR_API_KEY, SENDER, PASSWORD, RECEIVERS_STR]):
    logger.error("Missing required environment variables")
    exit(1)
RECEIVERS = [email.strip() for email in RECEIVERS_STR.split(",")]

# =========================
# FIREBASE SETUP
# =========================
FIREBASE_KEY = "firebase-key.json"
FIREBASE_URL = "https://aqi-email-bot-default-rtdb.asia-southeast1.firebasedatabase.app"

try:
    cred = credentials.Certificate(FIREBASE_KEY)
    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_URL})
except ValueError:
    pass

# =========================
# LOCATIONS
# =========================
locations = [
    {"name": "Calamba, Laguna", "lat": 14.1919, "lon": 121.0711},
    {"name": "Biñan, Laguna", "lat": 14.2769, "lon": 121.0589},
]
TAAL_LAT = 14.3568
TAAL_LON = 121.0064
PH_OFFSET = timedelta(hours=8)

# =========================
# AQI LABELS & COLORS
# =========================
aqi_map = {
    1: {"label": "Good",      "color": "#43a047", "advice": "Air quality is satisfactory.",                          "emoji": "😊"},
    2: {"label": "Fair",      "color": "#fbc02d", "advice": "Air quality is acceptable.",                            "emoji": "🙂"},
    3: {"label": "Moderate",  "color": "#fb8c00", "advice": "Sensitive groups should limit outdoor activity.",       "emoji": "⚠️"},
    4: {"label": "Poor",      "color": "#e53935", "advice": "Everyone should reduce prolonged outdoor activity.",    "emoji": "😷"},
    5: {"label": "Very Poor", "color": "#6a1b9a", "advice": "Avoid outdoor activity. Wear N95 masks if necessary.", "emoji": "🚫"},
}

def get_aqi_level(v):
    if v <= 50:   return 1
    elif v <= 100: return 2
    elif v <= 150: return 3
    elif v <= 200: return 4
    else:          return 5

def get_compass_direction(deg):
    if deg is None: return "N/A"
    try:
        dirs = ["North","NNE","Northeast","ENE","East","ESE","Southeast","SSE",
                "South","SSW","Southwest","WSW","West","WNW","Northwest","NNW"]
        return dirs[round(float(deg) / 22.5) % 16]
    except: return "N/A"

# =========================
# FETCH CURRENT AQI FROM IQAIR
# =========================
def get_current_aqi(lat, lon):
    try:
        url = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={IQAIR_API_KEY}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "success":
            return None
        pollution = data["data"]["current"]["pollution"]
        weather   = data["data"]["current"]["weather"]
        pm = {"p1": "PM10", "p2": "PM2.5", "o3": "O3", "n2": "NO2"}
        return {
            "aqi":            pollution.get("aqius"),
            "main_pollutant": pm.get(pollution.get("mainus"), pollution.get("mainus","N/A")),
            "temperature":    weather.get("tp"),
            "humidity":       weather.get("hu"),
            "wind_direction": weather.get("wd"),
            "wind_speed":     round(weather.get("ws", 0), 2),
        }
    except Exception as e:
        logger.error(f"IQAir fetch failed: {e}")
        return None

# =========================
# NEWS
# =========================
NEWS_EXCLUDE = ["pypi","mcp","software","stock market","crypto","football","basketball","celebrity","k-pop","recipe","horoscope"]
NEWS_QUERIES = [
    '(Calamba OR Biñan OR Binan) AND ("air quality" OR pollution OR haze OR smog)',
    '(Laguna province) AND ("air quality" OR pollution OR haze OR ashfall)',
    '(Taal OR "Taal Volcano") AND (eruption OR alert OR ash OR activity OR PHIVOLCS)',
    '(Laguna OR Calamba OR Biñan) AND (wildfire OR "forest fire" OR smoke OR "open burning")',
]

def _text(a): return f"{a.get('title','')} {a.get('description','')}".lower()
def _exclude(a): return any(k in _text(a) for k in NEWS_EXCLUDE)
def _recent(a):
    try:
        dt = datetime.fromisoformat(a.get("publishedAt","").replace("Z","+00:00")) + PH_OFFSET
        return dt >= datetime.utcnow() + PH_OFFSET - timedelta(days=7)
    except: return False

def _score(a):
    t, score, tags = _text(a), 0, []
    if any(x in t for x in ("taal","phivolcs","volcanic","eruption","ashfall")): score+=5; tags.append("🌋 Volcano/Taal")
    if any(x in t for x in ("air quality","aqi","pollution","pm2.5","smog")):    score+=4; tags.append("🌫️ Air quality")
    if any(x in t for x in ("haze","vog","smoke","wildfire","forest fire")):     score+=3; tags.append("🔥 Smoke/haze")
    if any(x in t for x in ("laguna","calamba","biñan","binan")):                score+=2; tags.append("📍 Laguna area")
    return score, tags

def get_news(max_articles=6):
    if not NEWS_API_KEY: return []
    seen, scored = set(), []
    for q in NEWS_QUERIES:
        try:
            r = requests.get("https://newsapi.org/v2/everything", params={"q":q,"sortBy":"publishedAt","language":"en","apiKey":NEWS_API_KEY,"pageSize":10}, timeout=10)
            r.raise_for_status()
            for a in r.json().get("articles",[]):
                link = a.get("url")
                if not link or link in seen or _exclude(a) or not _recent(a): continue
                sc, tags = _score(a)
                if sc < 2: continue
                seen.add(link); a["_score"]=sc; a["_tags"]=tags; scored.append(a)
        except Exception as e: logger.error(f"News error: {e}")
    return sorted(scored, key=lambda x: x["_score"], reverse=True)[:max_articles]

# =========================
# FIREBASE DAILY AVERAGES
# =========================
def get_daily_averages(location_name):
    try:
        data = db.reference(f"aqi_hourly/{location_name}").get()
        if not data: return []
        result = []
        for date_str in sorted(data.keys()):
            vals = [v.get("aqi") for v in data[date_str].values() if v.get("aqi") is not None]
            if vals:
                result.append({"date": date_str, "aqi": round(sum(vals)/len(vals))})
        logger.info(f"{location_name}: {len(result)} days loaded")
        return result
    except Exception as e:
        logger.error(f"Firebase error for {location_name}: {e}")
        return []

# =========================
# BUILD BAR CHART
# =========================
def build_bar_chart_plotly(location_name, daily_data):
    if not daily_data: return None
    try:
        values = [d["aqi"] for d in daily_data]
        max_y  = max(values) if values else 100
        y_max  = max_y + math.ceil(max_y * 0.15)
        colors = [aqi_map[get_aqi_level(v)]["color"] for v in values]
        labels = [str(v) if v > 100 else "" for v in values]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=list(range(1, len(values)+1)), y=values,
            marker=dict(color=colors, line=dict(width=0)),
            text=labels, textposition="outside",
            textfont=dict(size=11, color="#c62828", family="Arial"),
            showlegend=False,
        ))
        fig.add_hline(y=100, line=dict(color="rgba(211,47,47,0.3)", width=2.5, dash="dash"))
        fig.update_layout(
            title=dict(text=f"<b>{location_name} – 30-Day AQI History</b>", font=dict(size=16,color="#333",family="Arial"), x=0.5, xanchor="center"),
            xaxis=dict(tickfont=dict(size=11,color="#666",family="Arial"), gridcolor="rgba(0,0,0,0.05)", zeroline=False),
            yaxis=dict(range=[0, y_max], gridcolor="rgba(0,0,0,0.08)", zeroline=False, tickfont=dict(size=11,color="#666",family="Arial"), side="right", dtick=20),
            plot_bgcolor="rgba(250,250,250,0.6)", paper_bgcolor="white",
            margin=dict(l=80,r=80,t=80,b=60), width=1000, height=400,
        )
        safe = location_name.replace(",","").replace(" ","_").lower()
        path = f"/tmp/{safe}_chart.png"
        fig.write_image(path)
        logger.info(f"Chart saved: {path}")
        return path
    except Exception as e:
        logger.error(f"Chart error for {location_name}: {e}")
        return None

# =========================
# BUILD LOCATION CARD HTML
# =========================
def build_card(loc, aqi_data):
    if not aqi_data:
        return f"<td width='50%' style='padding:8px;vertical-align:top;'><p style='color:#999;font-size:13px;'>No data available for {html.escape(loc['name'])}</p></td>"

    aqi_value      = aqi_data.get("aqi", 0)
    aqi_info       = aqi_map.get(get_aqi_level(aqi_value), aqi_map[3])
    main_pollutant = aqi_data.get("main_pollutant", "N/A")
    temp           = aqi_data.get("temperature", "-")
    humidity       = aqi_data.get("humidity", "-")
    wind_speed     = aqi_data.get("wind_speed", "-")
    wind_compass   = get_compass_direction(aqi_data.get("wind_direction"))
    color          = aqi_info["color"]

    return f"""
<td width="50%" style="padding: 8px; vertical-align: top;">

  <!-- Location label -->
  <p style="font-family:Arial,sans-serif; font-size:13px; font-weight:bold; color:#333; margin:0 0 8px 0;">
    📍 {html.escape(loc['name'])}
  </p>

  <!-- Card -->
  <table width="100%" cellpadding="0" cellspacing="0" style="border-radius:10px; overflow:hidden; border:1px solid #e0e0e0;">

    <!-- AQI Header -->
    <tr>
      <td style="background-color:{color}; padding:16px; border-radius:10px 10px 0 0;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <!-- AQI number box -->
            <td width="64" style="vertical-align:middle;">
              <table cellpadding="0" cellspacing="0" style="background:rgba(255,255,255,0.22); border-radius:8px; width:64px;">
                <tr><td style="padding:10px 8px; text-align:center;">
                  <div style="font-family:Arial,sans-serif; font-size:30px; font-weight:bold; color:white; line-height:1;">{aqi_value}</div>
                  <div style="font-family:Arial,sans-serif; font-size:10px; color:white; margin-top:4px;">AQI</div>
                </td></tr>
              </table>
            </td>
            <!-- Label + advice -->
            <td style="padding-left:12px; vertical-align:middle;">
              <div style="font-family:Arial,sans-serif; font-size:15px; font-weight:bold; color:white; margin-bottom:4px;">{aqi_info['label']}</div>
              <div style="font-family:Arial,sans-serif; font-size:11px; color:white; opacity:0.95; line-height:1.4;">{aqi_info['advice']}</div>
            </td>
            <!-- Emoji -->
            <td width="40" style="vertical-align:middle; text-align:right; font-size:30px; padding-left:8px;">{aqi_info['emoji']}</td>
          </tr>
        </table>
      </td>
    </tr>

    <!-- Body -->
    <tr>
      <td style="background-color:#ffffff; padding:14px 16px;">

        <!-- Main pollutant -->
        <p style="font-family:Arial,sans-serif; font-size:12px; color:#555; margin:0 0 10px 0;">
          Main pollutant: <strong>{main_pollutant}</strong>
        </p>

        <!-- Wind info -->
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#e8f4fd; border-left:3px solid #1976d2; border-radius:0 4px 4px 0; margin-bottom:12px;">
          <tr><td style="padding:8px 10px;">
            <span style="font-family:Arial,sans-serif; font-size:11px; color:#1565c0;">
              💨 Wind direction: <strong>{wind_compass}</strong>
            </span>
          </td></tr>
        </table>

        <!-- Weather stats -->
        <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #f0f0f0; border-radius:6px; overflow:hidden;">
          <tr>
            <td width="33%" style="padding:10px 6px; text-align:center; background:#f9f9f9; border-right:1px solid #f0f0f0;">
              <div style="font-family:Arial,sans-serif; font-size:10px; color:#888; margin-bottom:4px;">Temperature</div>
              <div style="font-family:Arial,sans-serif; font-size:13px; font-weight:bold; color:#333;">{temp}°C</div>
            </td>
            <td width="34%" style="padding:10px 6px; text-align:center; background:#f9f9f9; border-right:1px solid #f0f0f0;">
              <div style="font-family:Arial,sans-serif; font-size:10px; color:#888; margin-bottom:4px;">Wind Speed</div>
              <div style="font-family:Arial,sans-serif; font-size:13px; font-weight:bold; color:#333;">{wind_speed} m/s</div>
            </td>
            <td width="33%" style="padding:10px 6px; text-align:center; background:#f9f9f9;">
              <div style="font-family:Arial,sans-serif; font-size:10px; color:#888; margin-bottom:4px;">Humidity</div>
              <div style="font-family:Arial,sans-serif; font-size:13px; font-weight:bold; color:#333;">{humidity}%</div>
            </td>
          </tr>
        </table>

      </td>
    </tr>

  </table>
</td>
"""

# =========================
# BUILD HTML EMAIL
# =========================
def build_html_email():
    # Fetch data
    location_data = {loc["name"]: get_current_aqi(loc["lat"], loc["lon"]) for loc in locations}

    # Header
    html_content = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0; padding:10px; background-color:#f5f5f5; font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td align="center">
<table width="900" cellpadding="0" cellspacing="0" style="background-color:white; border-radius:12px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,0.1);">

  <!-- Header -->
  <tr>
    <td style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); padding:32px 20px; text-align:center;">
      <div style="font-size:30px; font-weight:bold; color:white; margin-bottom:6px;">🌍 Air Quality Report</div>
      <div style="font-size:14px; color:white; opacity:0.95;">Weekly AQI Summary</div>
    </td>
  </tr>

  <!-- Cards -->
  <tr>
    <td style="padding:20px;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr>
"""

    # Build cards
    for loc in locations:
        html_content += build_card(loc, location_data.get(loc["name"]))

    html_content += """
        </tr>
      </table>
    </td>
  </tr>
"""

    # Charts
    logger.info("Fetching 30-day averages from Firebase...")
    chart_files = []

    for loc in locations:
        daily = get_daily_averages(loc["name"])
        if not daily:
            continue
        safe  = loc["name"].replace(",","").replace(" ","_").lower().replace("ñ","n")
        cid   = f"{safe}_chart"
        path  = build_bar_chart_plotly(loc["name"], daily)
        if path:
            html_content += f"""
  <!-- Chart: {loc['name']} -->
  <tr>
    <td style="padding:0 20px 20px 20px;">
      <div style="border-left:4px solid #667eea; padding-left:14px; margin-bottom:10px;">
        <div style="font-size:15px; font-weight:bold; color:#333;">📊 {html.escape(loc['name'])} – 30-Day AQI History</div>
        <div style="font-size:11px; color:#888; margin-top:3px;">Daily average AQI · Color-coded by severity</div>
      </div>
      <img src="cid:{cid}" width="100%" style="border-radius:6px; border:1px solid #e0e0e0; display:block;" alt="{html.escape(loc['name'])} AQI chart" />
    </td>
  </tr>
"""
            chart_files.append((cid, path))

    # News
    news_articles = get_news()
    if news_articles:
        html_content += """
  <!-- News -->
  <tr>
    <td style="padding:0 20px 20px 20px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#fff3e0; border-left:4px solid #ff6f00; border-radius:0 8px 8px 0; padding:16px;">
        <tr><td style="padding:16px;">
          <div style="font-size:15px; font-weight:bold; color:#ff6f00; margin-bottom:10px;">📰 This Week's Headlines</div>
"""
        for a in news_articles:
            title  = html.escape(a.get("title") or "No title")
            desc   = html.escape(a.get("description") or "")
            url    = html.escape(a.get("url") or "#", quote=True)
            source = html.escape((a.get("source") or {}).get("name","Unknown"))
            html_content += f"""
          <table width="100%" cellpadding="0" cellspacing="0" style="border-bottom:1px solid #ffe0b2; margin-bottom:10px; padding-bottom:10px;">
            <tr><td>
              <a href="{url}" style="font-size:13px; font-weight:bold; color:#ff6f00; text-decoration:none;">{title}</a><br>
              <span style="font-size:11px; color:#999;">{source}</span><br>
              <span style="font-size:12px; color:#333;">{desc}</span>
            </td></tr>
          </table>
"""
        html_content += """
        </td></tr>
      </table>
    </td>
  </tr>
"""

    # Footer
    html_content += """
  <!-- Footer -->
  <tr>
    <td style="background-color:#f5f5f5; padding:16px; text-align:center; border-top:1px solid #e0e0e0;">
      <div style="font-size:11px; color:#999;">Data sources: IQAir API, Open-Meteo API, NewsAPI</div>
      <div style="font-size:11px; color:#999; margin-top:4px;">This is an automated report. Please do not reply to this email.</div>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>
"""
    return html_content, chart_files

# =========================
# SEND EMAIL
# =========================
def send_email():
    try:
        html_email, chart_files = build_html_email()
        msg = MIMEMultipart("related")
        msg_alt = MIMEMultipart("alternative")
        msg.attach(msg_alt)
        msg["Subject"] = "🌍 Weekly AQI Report (Laguna)"
        msg["From"]    = SENDER
        msg["To"]      = ", ".join(RECEIVERS)
        msg_alt.attach(MIMEText(html_email, "html"))

        for chart_id, chart_path in chart_files:
            try:
                with open(chart_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"inline; filename={chart_id}.png")
                part.add_header("Content-ID", f"<{chart_id}>")
                msg.attach(part)
                logger.info(f"Attached: {chart_id}")
            except Exception as e:
                logger.error(f"Attachment error {chart_id}: {e}")

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        logger.info("Email sent successfully!")
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed.")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    send_email()
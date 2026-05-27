import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import os
import logging
import math
import json
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
    1: {"label": "Good", "color": "#43a047", "advice": "Air quality is satisfactory.", "emoji": "😊"},
    2: {"label": "Fair", "color": "#fbc02d", "advice": "Air quality is acceptable.", "emoji": "🙂"},
    3: {"label": "Moderate", "color": "#fb8c00", "advice": "Sensitive groups should limit outdoor activity.", "emoji": "⚠️"},
    4: {"label": "Poor", "color": "#e53935", "advice": "Everyone should reduce prolonged outdoor activity.", "emoji": "😷"},
    5: {"label": "Very Poor", "color": "#6a1b9a", "advice": "Avoid outdoor activity. Wear N95 masks if necessary.", "emoji": "🚫"}
}

# =========================
# AQI LEVEL FROM VALUE
# =========================
def get_aqi_level(aqi_value):
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
# FETCH CURRENT AQI FROM IQAIR
# =========================
def get_current_aqi(lat, lon):
    """Fetch current AQI from IQAir"""
    try:
        url = f"http://api.airvisual.com/v2/nearest_city?lat={lat}&lon={lon}&key={IQAIR_API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get("status") != "success":
            logger.error(f"IQAir API error: {data.get('data')}")
            return None
        
        pollution = data["data"]["current"]["pollution"]
        weather = data["data"]["current"]["weather"]
        
        # Map main pollutant code to name
        pollutant_map = {"p1": "PM10", "p2": "PM2.5", "o3": "O3", "n2": "NO2"}
        main_pollutant = pollutant_map.get(pollution.get("mainus"), pollution.get("mainus", "N/A"))
        
        return {
            "aqi": pollution.get("aqius"),
            "main_pollutant": main_pollutant,
            "temperature": weather.get("tp"),
            "humidity": weather.get("hu"),
            "wind_direction": weather.get("wd"),
            "wind_speed": round(weather.get("ws", 0), 2)
        }
    except Exception as e:
        logger.error(f"Failed to fetch current AQI: {e}")
        return None

# =========================
# NEWS
# =========================
NEWS_EXCLUDE_KEYWORDS = [
    "pypi", "mcp", "data-mcp", "software", "stock market", "crypto",
    "football", "basketball", "celebrity", "k-pop", "recipe", "horoscope",
]

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
    try:
        article_date = datetime.fromisoformat(article_date_str.replace("Z", "+00:00"))
        article_date_ph = article_date + PH_OFFSET
        now_ph = datetime.utcnow() + PH_OFFSET
        one_week_ago = now_ph - timedelta(days=7)
        return article_date_ph >= one_week_ago
    except (ValueError, TypeError):
        logger.warning(f"Could not parse article date: {article_date_str}")
        return False

def _score_and_tag_article(article):
    text = _article_text(article)
    score = 0
    tags = []

    if any(t in text for t in ("taal", "phivolcs", "volcanic ash", "volcano", "eruption", "ashfall", "ash fall", "volcanic activity")):
        score += 5
        tags.append("🌋 Volcano / Taal")

    if any(t in text for t in ("air quality", "aqi", "pollution", "pm2.5", "pm10", "smog", "particulate")):
        score += 4
        tags.append("🌫️ Air quality")

    if any(t in text for t in ("haze", "vog", "smoke", "wildfire", "forest fire", "open burning")):
        score += 3
        tags.append("🔥 Smoke / haze")

    if any(t in text for t in ("laguna", "calamba", "biñan", "binan")):
        score += 2
        tags.append("📍 Laguna / Calamba / Biñan")

    return score, tags

def get_air_quality_news(max_articles=6):
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
    for data in fetched_aqi.values():
        if not data:
            continue
        if data.get("aqi", 0) > 100:
            return True
    return False

# =========================
# GET DAILY AVERAGES FROM HOURLY DATA
# =========================
def get_daily_averages(location_name):
    """Fetch hourly data and calculate daily averages for past 30 days"""
    try:
        ref = db.reference(f"aqi_hourly/{location_name}")
        hourly_data = ref.get()
        
        if not hourly_data:
            logger.warning(f"No hourly data for {location_name}")
            return []
        
        daily_averages = []
        for date_str in sorted(hourly_data.keys()):
            hourly_readings = hourly_data[date_str]
            aqi_values = [v.get("aqi") for v in hourly_readings.values() if v.get("aqi") is not None]
            
            if aqi_values:
                avg_aqi = round(sum(aqi_values) / len(aqi_values))
                daily_averages.append({"date": date_str, "aqi": avg_aqi})
                logger.info(f"Daily avg: {location_name} | {date_str} | AQI: {avg_aqi} (from {len(aqi_values)} readings)")
        
        logger.info(f"Loaded {len(daily_averages)} days for {location_name}")
        return daily_averages
    except Exception as e:
        logger.error(f"Failed to fetch daily averages for {location_name}: {e}")
        return []

# =========================
# BUILD BAR CHART
# =========================
def build_bar_chart_plotly(location_name, daily_data):
    """Build bar chart from daily averages"""
    if not daily_data or len(daily_data) == 0:
        logger.warning(f"No data for {location_name} chart")
        return None
    
    try:
        logger.info(f"Building chart for {location_name}")
        
        values = [d["aqi"] for d in daily_data]
        all_valid = [v for v in values if v is not None]
        max_y = max(all_valid) if all_valid else 100
        y_max = max_y + math.ceil(max_y * 0.15)

        day_numbers = list(range(1, len(values) + 1))
        
        colors = []
        text_labels = []
        for v in values:
            if v is not None:
                color = aqi_map[get_aqi_level(v)]["color"]
                text = str(int(v)) if v > 100 else ""
            else:
                color = "#ccc"
                text = ""
            colors.append(color)
            text_labels.append(text)

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=day_numbers,
            y=values,
            marker=dict(color=colors, line=dict(width=0)),
            text=text_labels,
            textposition="outside",
            textfont=dict(size=11, color="#c62828", family="Arial"),
            hovertemplate="<b>Day %{x}</b><br>AQI: %{y}<extra></extra>",
            showlegend=False
        ))

        fig.add_hline(
            y=100,
            line=dict(color="rgba(211, 47, 47, 0.3)", width=2.5, dash="dash"),
            name="Threshold (100)"
        )

        fig.update_layout(
            title=dict(
                text=f"<b>{location_name} - 30-Day AQI History</b>",
                font=dict(size=16, color="#333", family="Arial"),
                x=0.5,
                xanchor="center"
            ),
            xaxis=dict(
                title=None,
                tickfont=dict(size=11, color="#666", family="Arial"),
                gridcolor="rgba(0,0,0,0.05)",
                showgrid=True,
                zeroline=False
            ),
            yaxis=dict(
                title=None,
                range=[0, y_max],
                gridcolor="rgba(0,0,0,0.08)",
                zeroline=False,
                tickfont=dict(size=11, color="#666", family="Arial"),
                side="right",
                dtick=20
            ),
            plot_bgcolor="rgba(250,250,250,0.6)",
            paper_bgcolor="white",
            margin=dict(l=80, r=80, t=80, b=60),
            width=1000,
            height=400,
            showlegend=True,
            hovermode="x unified",
            font=dict(family="Arial", size=11, color="#333")
        )

        safe_name = location_name.replace(",", "").replace(" ", "_").lower()
        filepath = f"/tmp/{safe_name}_chart.png"
        fig.write_image(filepath)
        
        logger.info(f"Chart saved: {filepath}")
        return filepath

    except Exception as e:
        logger.error(f"Failed to generate chart for {location_name}: {e}", exc_info=True)
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
            .cards-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; padding: 20px; }
            .aqi-card { border-radius: 8px; padding: 1.25rem; color: white; }
            .card-location { font-size: 14px; font-weight: bold; margin-bottom: 1rem; }
            .card-content { display: flex; gap: 0.75rem; align-items: flex-start; margin-bottom: 1rem; }
            .aqi-box { background: rgba(255,255,255,0.2); border-radius: 6px; padding: 0.6rem; text-align: center; min-width: 70px; flex-shrink: 0; }
            .aqi-value { font-size: 28px; font-weight: 500; line-height: 1; }
            .aqi-label { font-size: 10px; margin-top: 4px; }
            .card-text { flex: 1; font-size: 13px; }
            .card-title { font-weight: 500; margin-bottom: 0.3rem; line-height: 1.2; }
            .card-advice { opacity: 0.9; font-size: 12px; line-height: 1.3; }
            .card-emoji { font-size: 32px; line-height: 1; flex-shrink: 0; }
            .card-divider { border-top: 1px solid rgba(255,255,255,0.25); padding-top: 10px; font-size: 12px; }
            .weather-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; padding: 0 20px 20px 20px; }
            .weather-card { background-color: #f9f9f9; border-radius: 8px; padding: 1rem; border: 0.5px solid #e0e0e0; display: flex; justify-content: space-around; align-items: center; }
            .weather-item { text-align: center; }
            .weather-icon { font-size: 20px; margin-bottom: 4px; }
            .weather-value { font-size: 14px; font-weight: 500; color: #333; }
            .weather-label { font-size: 10px; color: #888; }
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
    """
    
    # Fetch current AQI for both locations
    fetched_aqi = {}
    location_data = {}
    
    for loc in locations:
        aqi_data = get_current_aqi(loc["lat"], loc["lon"])
        fetched_aqi[loc["name"]] = aqi_data
        location_data[loc["name"]] = aqi_data
        
        if not aqi_data:
            logger.warning(f"No AQI data for {loc['name']}")
    
    # Build AQI cards (2 columns)
    html_content += '<div class="cards-grid">'
    
    for loc in locations:
        aqi_data = location_data.get(loc["name"])
        if not aqi_data:
            continue
        
        aqi_value = aqi_data.get("aqi", 0)
        aqi_level = get_aqi_level(aqi_value)
        aqi_info = aqi_map.get(aqi_level, aqi_map[3])
        main_pollutant = aqi_data.get("main_pollutant", "N/A")
        
        card_html = f"""
            <div class="aqi-card" style="background-color: {aqi_info['color']};">
                <div class="card-location">📍 {html.escape(loc['name'])}</div>
                
                <div class="card-content">
                    <div class="aqi-box">
                        <div class="aqi-value">{aqi_value}</div>
                        <div class="aqi-label">AQI</div>
                    </div>
                    
                    <div class="card-text">
                        <div class="card-title">{aqi_info['label']}</div>
                        <div class="card-advice">{aqi_info['advice']}</div>
                    </div>
                    
                    <div class="card-emoji">{aqi_info['emoji']}</div>
                </div>
                
                <div class="card-divider">
                    <strong>Main: {main_pollutant}</strong>
                </div>
            </div>
        """
        html_content += card_html
    
    html_content += '</div>'
    
    # Build weather cards (2 columns)
    html_content += '<div class="weather-grid">'
    
    for loc in locations:
        aqi_data = location_data.get(loc["name"])
        if not aqi_data:
            continue
        
        temp = aqi_data.get("temperature", "-")
        humidity = aqi_data.get("humidity", "-")
        wind_direction = aqi_data.get("wind_direction", "-")
        wind_speed = aqi_data.get("wind_speed", "-")
        
        weather_html = f"""
            <div class="weather-card">
                <div class="weather-item">
                    <div class="weather-icon">🌡️</div>
                    <div class="weather-value">{temp}°C</div>
                    <div class="weather-label">Temp</div>
                </div>
                
                <div class="weather-item">
                    <div class="weather-icon">🧭</div>
                    <div class="weather-value">{wind_direction}°</div>
                    <div class="weather-label">Wind</div>
                </div>
                
                <div class="weather-item">
                    <div class="weather-icon">💧</div>
                    <div class="weather-value">{humidity}%</div>
                    <div class="weather-label">Humidity</div>
                </div>
            </div>
        """
        html_content += weather_html
    
    html_content += '</div>'
    
    # Fetch and build charts
    logger.info("Fetching 30-day daily averages...")
    cal = locations[0]
    bin_ = locations[1]
    
    cal_daily = get_daily_averages(cal["name"])
    bin_daily = get_daily_averages(bin_["name"])
    
    chart_files = []
    
    if cal_daily:
        cal_chart_path = build_bar_chart_plotly("Calamba, Laguna", cal_daily)
        if cal_chart_path:
            html_content += f"""
            <div class="divider"></div>
            <div class="trend-section">
                <p class="trend-title">📊 Calamba - 30-Day AQI History</p>
                <p class="trend-subtitle">Daily average AQI · Color-coded by severity level</p>
                <img src="cid:calamba_chart" alt="Calamba 30-day AQI" class="trend-img" />
            </div>
            """
            chart_files.append(("calamba_chart", cal_chart_path))
    
    if bin_daily:
        bin_chart_path = build_bar_chart_plotly("Biñan, Laguna", bin_daily)
        if bin_chart_path:
            html_content += f"""
            <div class="trend-section">
                <p class="trend-title">📊 Biñan - 30-Day AQI History</p>
                <p class="trend-subtitle">Daily average AQI · Color-coded by severity level</p>
                <img src="cid:binan_chart" alt="Biñan 30-day AQI" class="trend-img" />
            </div>
            """
            chart_files.append(("binan_chart", bin_chart_path))
    
    # NEWS
    news_articles = get_air_quality_news()
    elevated_aqi = _has_elevated_aqi(fetched_aqi)
    if news_articles:
        html_content += """
        <div class="divider"></div>
        <div class="news-section">
            <h3 class="news-title">📰 This Week's Air Quality & Environment Headlines</h3>
            <p class="news-subtitle">News from the Laguna region and Taal area that may affect air quality</p>
        """
        if elevated_aqi:
            html_content += """
            <p class="news-intro">⚠️ Elevated AQI detected. Related headlines below.</p>
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
    
    html_content += """
            <div class="footer">
                <p>Data sources: IQAir API, Open-Meteo API, NewsAPI</p>
                <p>This is an automated report. Please do not reply to this email.</p>
            </div>
        </div>
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
        msg_alternative = MIMEMultipart("alternative")
        msg.attach(msg_alternative)
        
        msg["Subject"] = "🌍 Weekly AQI & Weather Report (Laguna)"
        msg["From"] = SENDER
        msg["To"] = ", ".join(RECEIVERS)
        
        msg_alternative.attach(MIMEText(html_email, "html"))
        
        for chart_id, chart_path in chart_files:
            try:
                with open(chart_path, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"inline; filename={chart_id}.png")
                part.add_header("Content-ID", f"<{chart_id}>")
                msg.attach(part)
                logger.info(f"Attached chart: {chart_id}")
            except Exception as e:
                logger.error(f"Failed to attach chart {chart_id}: {e}")
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER, PASSWORD)
            server.sendmail(SENDER, RECEIVERS, msg.as_string())
        logger.info("Email sent successfully!")
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed.")
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
    except Exception as e:
        logger.error(f"Error: {e}")

if __name__ == "__main__":
    send_email()
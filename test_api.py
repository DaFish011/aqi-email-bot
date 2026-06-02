import requests
import os

IQAIR_API_KEY = os.getenv("IQAIR_API_KEY", "YOUR_API_KEY_HERE")

print("=" * 90)
print("Testing IQAir API - Official Binan City Coordinates from /city endpoint")
print("=" * 90)

# Official API Binan city coordinates (14.2655, 121.4338)
center_lat = 14.2655
center_lon = 121.4338

test_points = [
    {"name": "Official Binan API Coordinates", "lat": center_lat, "lon": center_lon},
    {"name": "Binan Point 1 (0.01° North)", "lat": center_lat + 0.01, "lon": center_lon},
    {"name": "Binan Point 2 (0.01° South)", "lat": center_lat - 0.01, "lon": center_lon},
    {"name": "Binan Point 3 (0.01° East)", "lat": center_lat, "lon": center_lon + 0.01},
    {"name": "Binan Point 4 (0.01° West)", "lat": center_lat, "lon": center_lon - 0.01},
]

for point in test_points:
    url = f"http://api.airvisual.com/v2/nearest_city?lat={point['lat']}&lon={point['lon']}&key={IQAIR_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get("status") == "success":
            city_data = data.get("data", {})
            station = city_data.get('city')
            aqi = city_data.get('current', {}).get('pollution', {}).get('aqius')
            api_coords = city_data.get('location', {}).get('coordinates')
            
            print(f"\n{point['name']}")
            print(f"  Test Coordinates: {point['lat']:.6f}°N, {point['lon']:.6f}°E")
            print(f"  → Returned Station: {station}")
            print(f"  → AQI: {aqi}")
            print(f"  → API Coords: {api_coords}")
        else:
            print(f"\n❌ {point['name']}: {data.get('data')}")
    except Exception as e:
        print(f"\n❌ {point['name']}: {e}")

print("\n" + "=" * 90)
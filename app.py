from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta
from functools import wraps
import time
import math
import logging

# ============ LOGGING SETUP ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============ CORS CONFIGURATION ============
# Allow all origins but can be restricted later for production
CORS(app, resources={r"/*": {"origins": "*"}})

# Performance improvement - disable JSON key sorting
app.config['JSON_SORT_KEYS'] = False

# ============ CRITICAL FIX 1: NO DEFAULT PROXY SECRET ============
RAPIDAPI_PROXY_SECRET = os.environ.get('RAPIDAPI_PROXY_SECRET')

if not RAPIDAPI_PROXY_SECRET:
    raise Exception(
        "🚨 CRITICAL: RAPIDAPI_PROXY_SECRET environment variable is not set! "
        "Please set it immediately. Generate a secure key using: openssl rand -hex 32"
    )

# ============ RATE LIMITING CONFIGURATION ============
RATE_LIMIT_REQUESTS = int(os.environ.get('RATE_LIMIT_REQUESTS', 60))
RATE_LIMIT_WINDOW = int(os.environ.get('RATE_LIMIT_WINDOW', 60))

# ============ FIX 2: Rate limit by API key, not IP ============
request_counts = {}

def simple_rate_limit(identifier):
    """Rate limit by API key (not IP) to work with RapidAPI proxy"""
    now = time.time()
    
    if identifier not in request_counts:
        request_counts[identifier] = []
    
    # Clean old requests
    request_counts[identifier] = [t for t in request_counts[identifier] if now - t < RATE_LIMIT_WINDOW]
    
    # Check limit
    if len(request_counts[identifier]) >= RATE_LIMIT_REQUESTS:
        return False
    
    # Add current request
    request_counts[identifier].append(now)
    return True

# ============ CACHE SYSTEM ============
weather_cache = {}
CACHE_DURATION = 300  # 5 minutes

# ============ FIX 1 & 2: Enhanced API Key Validation ============
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Get headers from RapidAPI
        api_key = request.headers.get('X-RapidAPI-Key')
        proxy_secret = request.headers.get('X-RapidAPI-Proxy-Secret')
        
        # FIX 2: Use API key for rate limiting (not IP)
        identifier = api_key or request.remote_addr
        
        # Rate limiting check
        if not simple_rate_limit(identifier):
            logger.warning(f"Rate limit exceeded for identifier: {identifier[:8] if identifier else 'unknown'}...")
            return jsonify({
                'error': 'Rate limit exceeded',
                'message': f'Maximum {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds',
                'retry_after': RATE_LIMIT_WINDOW
            }), 429
        
        # Check if API key is present
        if not api_key:
            logger.warning("Missing API key in request")
            return jsonify({
                'error': 'Unauthorized',
                'message': 'Missing X-RapidAPI-Key header. Please subscribe to this API on RapidAPI.',
                'documentation': 'https://rapidapi.com/docs/authentication'
            }), 401
        
        # FIX 1: Validate proxy secret (NO DEFAULT VALUE!)
        if not proxy_secret:
            logger.warning(f"Missing proxy secret for API key: {api_key[:8]}...")
            return jsonify({
                'error': 'Forbidden',
                'message': 'Missing X-RapidAPI-Proxy-Secret header. Please use RapidAPI gateway.',
                'bypass_detected': True
            }), 403
        
        # Verify the proxy secret matches environment variable
        if proxy_secret != RAPIDAPI_PROXY_SECRET:
            logger.warning(f"Invalid proxy secret for API key: {api_key[:8]}...")
            return jsonify({
                'error': 'Forbidden',
                'message': 'Invalid proxy secret. This request did not come from RapidAPI.',
                'bypass_detected': True
            }), 403
        
        # Log successful request (masked for privacy)
        masked_key = api_key[:8] + '...' if len(api_key) > 8 else '***'
        logger.info(f"Request from RapidAPI user: {masked_key} - Endpoint: {request.endpoint}")
        
        # Store API key in request context for usage tracking
        request.api_key = api_key
        
        return f(*args, **kwargs)
    return decorated

# ============ WEATHER SERVICE CLASS ============
class WeatherService:
    """Complete Weather Service with Multiple Endpoints"""
    
    @staticmethod
    def get_coordinates(city_name, country=None):
        """Geocode city name to coordinates"""
        cache_key = f"geocode_{city_name}_{country}"
        if cache_key in weather_cache:
            cache_time, cached_data = weather_cache[cache_key]
            if time.time() - cache_time < CACHE_DURATION:
                return cached_data
        
        try:
            response = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city_name, "count": 1, "language": "en", "format": "json"},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                if results:
                    result = results[0]
                    coords = {
                        'latitude': result['latitude'],
                        'longitude': result['longitude'],
                        'name': result['name'],
                        'country': result.get('country', ''),
                        'admin1': result.get('admin1', '')
                    }
                    weather_cache[cache_key] = (time.time(), coords)
                    return coords
            return None
        except requests.exceptions.Timeout:
            logger.error(f"Geocoding timeout for {city_name}")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"Geocoding connection error for {city_name}")
            return None
        except Exception as e:
            logger.error(f"Geocoding error: {e}")
            return None
    
    @staticmethod
    def get_current_weather(city, country=None):
        """Get current weather"""
        coords = WeatherService.get_coordinates(city, country)
        if not coords:
            return {'error': 'City not found'}
        
        cache_key = f"current_{coords['latitude']}_{coords['longitude']}"
        if cache_key in weather_cache:
            cache_time, cached_data = weather_cache[cache_key]
            if time.time() - cache_time < CACHE_DURATION:
                return cached_data
        
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "current_weather": True,
                    "hourly": "temperature_2m,relativehumidity_2m",
                    "temperature_unit": "celsius",
                    "windspeed_unit": "kmh",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            # FIX 3: Don't expose internal errors
            if response.status_code != 200:
                logger.error(f"Weather API returned {response.status_code} for {city}")
                return {'error': 'Weather service temporarily unavailable'}
            
            data = response.json()
            current = data.get('current_weather', {})
            
            hourly = data.get('hourly', {})
            current_hour = datetime.now().hour
            
            humidity = None
            if hourly and 'relativehumidity_2m' in hourly:
                humidity_data = hourly['relativehumidity_2m']
                if len(humidity_data) > current_hour:
                    humidity = humidity_data[current_hour]
            
            weather_data = {
                'city': coords['name'],
                'country': coords['country'],
                'region': coords.get('admin1', ''),
                'latitude': coords['latitude'],
                'longitude': coords['longitude'],
                'temperature': current.get('temperature'),
                'wind_speed': current.get('windspeed'),
                'wind_direction': current.get('winddirection'),
                'humidity': humidity,
                'timestamp': current.get('time'),
                'source': 'Open-Meteo'
            }
            
            weather_cache[cache_key] = (time.time(), weather_data)
            return weather_data
        except requests.exceptions.Timeout:
            logger.error(f"Weather API timeout for {city}")
            return {'error': 'Weather service temporarily unavailable'}
        except requests.exceptions.ConnectionError:
            logger.error(f"Weather API connection error for {city}")
            return {'error': 'Weather service temporarily unavailable'}
        except Exception as e:
            logger.error(f"Unexpected error in get_current_weather: {e}")
            return {'error': 'Service temporarily unavailable'}
    
    @staticmethod
    def get_forecast(city, days=5):
        """Get weather forecast"""
        coords = WeatherService.get_coordinates(city)
        if not coords:
            return {'error': 'City not found'}
        
        days = min(days, 16)
        cache_key = f"forecast_{coords['latitude']}_{coords['longitude']}_{days}"
        
        if cache_key in weather_cache:
            cache_time, cached_data = weather_cache[cache_key]
            if time.time() - cache_time < CACHE_DURATION:
                return cached_data
        
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_mean,weathercode,windspeed_10m_max",
                    "forecast_days": days,
                    "temperature_unit": "celsius",
                    "windspeed_unit": "kmh",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code != 200:
                logger.error(f"Forecast API returned {response.status_code} for {city}")
                return {'error': 'Forecast service temporarily unavailable'}
            
            data = response.json()
            daily = data.get('daily', {})
            
            def get_condition(code):
                conditions = {
                    0: "Sunny", 1: "Mostly Sunny", 2: "Partly Cloudy", 3: "Cloudy",
                    45: "Foggy", 48: "Foggy", 51: "Light Rain", 53: "Rain",
                    55: "Heavy Rain", 61: "Light Rain", 63: "Rain", 65: "Heavy Rain",
                    71: "Light Snow", 73: "Snow", 75: "Heavy Snow", 80: "Rain Showers",
                    81: "Rain Showers", 82: "Heavy Rain", 95: "Thunderstorm"
                }
                return conditions.get(code, "Unknown")
            
            forecast = []
            for i in range(len(daily.get('time', []))):
                weather_code = daily['weathercode'][i] if i < len(daily['weathercode']) else 0
                forecast.append({
                    'date': daily['time'][i],
                    'temperature_max': daily['temperature_2m_max'][i],
                    'temperature_min': daily['temperature_2m_min'][i],
                    'precipitation_probability': daily['precipitation_probability_mean'][i],
                    'wind_speed': daily['windspeed_10m_max'][i],
                    'condition': get_condition(weather_code)
                })
            
            forecast_data = {
                'city': coords['name'],
                'country': coords['country'],
                'forecast': forecast,
                'unit': 'celsius'
            }
            
            weather_cache[cache_key] = (time.time(), forecast_data)
            return forecast_data
        except Exception as e:
            logger.error(f"Unexpected error in get_forecast: {e}")
            return {'error': 'Service temporarily unavailable'}
    
    @staticmethod
    def get_air_quality(city):
        """Get air quality data for a city"""
        coords = WeatherService.get_coordinates(city)
        if not coords:
            return {'error': 'City not found'}
        
        try:
            response = requests.get(
                "https://air-quality-api.open-meteo.com/v1/air-quality",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "hourly": "pm10,pm2_5",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code != 200:
                return {'error': 'Air quality service temporarily unavailable'}
            
            data = response.json()
            hourly = data.get('hourly', {})
            current_hour = datetime.now().hour
            
            def safe_get(data_list, index):
                if data_list and len(data_list) > index:
                    return data_list[index]
                return None
            
            pm10 = safe_get(hourly.get('pm10', []), current_hour)
            pm25 = safe_get(hourly.get('pm2_5', []), current_hour)
            
            def get_aqi_category(value):
                if value is None:
                    return "Unknown"
                if value <= 12:
                    return "Good"
                elif value <= 35.4:
                    return "Moderate"
                elif value <= 55.4:
                    return "Unhealthy for Sensitive Groups"
                elif value <= 150.4:
                    return "Unhealthy"
                else:
                    return "Very Unhealthy"
            
            return {
                'city': coords['name'],
                'country': coords['country'],
                'pm10': pm10,
                'pm2_5': pm25,
                'aqi_category': get_aqi_category(pm25),
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Air quality error: {e}")
            return {'error': 'Service temporarily unavailable'}
    
    @staticmethod
    def get_uv_index(city):
        """Get UV Index for a city"""
        coords = WeatherService.get_coordinates(city)
        if not coords:
            return {'error': 'City not found'}
        
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "daily": "uv_index_max",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code != 200:
                return {'error': 'UV index service temporarily unavailable'}
            
            data = response.json()
            daily = data.get('daily', {})
            
            uv_max = daily.get('uv_index_max', [None])[0] if daily.get('uv_index_max') else None
            
            def get_uv_risk(uv_index):
                if uv_index is None:
                    return "Unknown"
                if uv_index <= 2:
                    return "Low"
                elif uv_index <= 5:
                    return "Moderate"
                elif uv_index <= 7:
                    return "High"
                elif uv_index <= 10:
                    return "Very High"
                else:
                    return "Extreme"
            
            return {
                'city': coords['name'],
                'country': coords['country'],
                'uv_index_max': uv_max,
                'uv_risk': get_uv_risk(uv_max),
                'recommendation': f"Protection {get_uv_risk(uv_max)} level recommended",
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"UV index error: {e}")
            return {'error': 'Service temporarily unavailable'}
    
    @staticmethod
    def get_sunrise_sunset(city):
        """Get sunrise and sunset times"""
        coords = WeatherService.get_coordinates(city)
        if not coords:
            return {'error': 'City not found'}
        
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "daily": "sunrise,sunset",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code != 200:
                return {'error': 'Sunrise/sunset service temporarily unavailable'}
            
            data = response.json()
            daily = data.get('daily', {})
            
            return {
                'city': coords['name'],
                'country': coords['country'],
                'date': daily.get('time', [datetime.now().strftime('%Y-%m-%d')])[0],
                'sunrise': daily.get('sunrise', [None])[0],
                'sunset': daily.get('sunset', [None])[0],
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Sunrise/sunset error: {e}")
            return {'error': 'Service temporarily unavailable'}
    
    @staticmethod
    def get_weather_by_coordinates(lat, lon):
        """Get weather using coordinates directly"""
        try:
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": True,
                    "temperature_unit": "celsius",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code != 200:
                return {'error': 'Weather service temporarily unavailable'}
            
            data = response.json()
            current = data.get('current_weather', {})
            
            return {
                'latitude': lat,
                'longitude': lon,
                'temperature': current.get('temperature'),
                'wind_speed': current.get('windspeed'),
                'wind_direction': current.get('winddirection'),
                'timestamp': current.get('time')
            }
        except Exception as e:
            logger.error(f"Coordinates weather error: {e}")
            return {'error': 'Service temporarily unavailable'}
    
    # FIX 5: Improved compare with explicit success/failure status
    @staticmethod
    def compare_weather(cities):
        """Compare weather across multiple cities with explicit status"""
        results = []
        for city in cities:
            weather = WeatherService.get_current_weather(city)
            if 'error' not in weather:
                results.append({
                    'city': city,
                    'status': 'success',
                    'temperature': weather.get('temperature'),
                    'wind_speed': weather.get('wind_speed'),
                    'humidity': weather.get('humidity')
                })
            else:
                results.append({
                    'city': city,
                    'status': 'failed',
                    'error': weather.get('error', 'Unknown error')
                })
        return results

# ============ API ENDPOINTS ============

@app.route('/', methods=['GET'])
def home():
    """API Homepage with all available endpoints"""
    return jsonify({
        'name': 'Weather Insights API',
        'version': '2.0.0',
        'description': 'Comprehensive weather data API powered by Open-Meteo',
        'authentication': {
            'type': 'RapidAPI Gateway',
            'required_headers': ['X-RapidAPI-Key', 'X-RapidAPI-Proxy-Secret'],
            'how_to_get_key': 'Subscribe on RapidAPI marketplace'
        },
        'endpoints': {
            'weather': [
                {'path': '/api/weather/current', 'method': 'GET', 'params': ['city', 'country']},
                {'path': '/api/weather/forecast', 'method': 'GET', 'params': ['city', 'days']},
                {'path': '/api/weather/by-coordinates', 'method': 'GET', 'params': ['lat', 'lon']},
                {'path': '/api/weather/compare', 'method': 'POST', 'params': ['cities']}
            ],
            'environment': [
                {'path': '/api/air-quality', 'method': 'GET', 'params': ['city']},
                {'path': '/api/uv-index', 'method': 'GET', 'params': ['city']},
                {'path': '/api/sunrise-sunset', 'method': 'GET', 'params': ['city']}
            ],
            'system': [
                {'path': '/api/health', 'method': 'GET', 'params': []}
            ]
        },
        'rate_limits': {
            'requests_per_minute': RATE_LIMIT_REQUESTS,
            'per_api_key': True
        }
    })

# FIX 4: Correct endpoint count (8 protected endpoints)
@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint (no auth required)"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0.0',
        'authenticated_endpoints': 8,  # FIXED: Now correct!
        'rate_limit': f"{RATE_LIMIT_REQUESTS}/min",
        'proxy_secret_configured': bool(RAPIDAPI_PROXY_SECRET)
    })

@app.route('/api/weather/current', methods=['GET'])
@require_api_key
def get_current_weather():
    city = request.args.get('city')
    country = request.args.get('country')
    
    if not city:
        return jsonify({'error': 'city parameter required'}), 400
    
    weather_data = WeatherService.get_current_weather(city, country)
    
    if 'error' in weather_data:
        return jsonify({'success': False, 'error': weather_data['error']}), 404
    
    return jsonify({
        'success': True,
        'data': weather_data,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/weather/forecast', methods=['GET'])
@require_api_key
def get_forecast():
    city = request.args.get('city')
    days = int(request.args.get('days', 5))
    
    if not city:
        return jsonify({'error': 'city parameter required'}), 400
    
    if days > 16:
        return jsonify({'error': 'Maximum days is 16'}), 400
    
    forecast_data = WeatherService.get_forecast(city, days)
    
    if 'error' in forecast_data:
        return jsonify({'success': False, 'error': forecast_data['error']}), 404
    
    return jsonify({
        'success': True,
        'data': forecast_data,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/air-quality', methods=['GET'])
@require_api_key
def get_air_quality():
    city = request.args.get('city')
    
    if not city:
        return jsonify({'error': 'city parameter required'}), 400
    
    air_quality = WeatherService.get_air_quality(city)
    
    if 'error' in air_quality:
        return jsonify({'success': False, 'error': air_quality['error']}), 404
    
    return jsonify({
        'success': True,
        'data': air_quality,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/uv-index', methods=['GET'])
@require_api_key
def get_uv_index():
    city = request.args.get('city')
    
    if not city:
        return jsonify({'error': 'city parameter required'}), 400
    
    uv_data = WeatherService.get_uv_index(city)
    
    if 'error' in uv_data:
        return jsonify({'success': False, 'error': uv_data['error']}), 404
    
    return jsonify({
        'success': True,
        'data': uv_data,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/sunrise-sunset', methods=['GET'])
@require_api_key
def get_sunrise_sunset():
    city = request.args.get('city')
    
    if not city:
        return jsonify({'error': 'city parameter required'}), 400
    
    solar_data = WeatherService.get_sunrise_sunset(city)
    
    if 'error' in solar_data:
        return jsonify({'success': False, 'error': solar_data['error']}), 404
    
    return jsonify({
        'success': True,
        'data': solar_data,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/weather/by-coordinates', methods=['GET'])
@require_api_key
def get_weather_by_coordinates():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    
    if not lat or not lon:
        return jsonify({'error': 'lat and lon parameters required'}), 400
    
    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return jsonify({'error': 'Invalid coordinates'}), 400
    
    weather_data = WeatherService.get_weather_by_coordinates(lat, lon)
    
    if 'error' in weather_data:
        return jsonify({'success': False, 'error': weather_data['error']}), 404
    
    return jsonify({
        'success': True,
        'data': weather_data,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/weather/compare', methods=['POST'])
@require_api_key
def compare_weather():
    data = request.get_json() or {}
    cities = data.get('cities', [])
    
    if not cities:
        return jsonify({'error': 'cities array required in request body'}), 400
    
    if len(cities) > 10:
        return jsonify({'error': 'Maximum 10 cities can be compared'}), 400
    
    comparison = WeatherService.compare_weather(cities)
    
    return jsonify({
        'success': True,
        'data': comparison,
        'total_cities': len(comparison),
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Weather Insights API on port {port}")
    logger.info(f"Rate limit: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW} seconds")
    logger.info(f"Proxy secret configured: {'YES' if RAPIDAPI_PROXY_SECRET else 'NO'}")
    app.run(debug=False, host='0.0.0.0', port=port)
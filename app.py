from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta
import hashlib
import hmac
import json
from functools import wraps
import time

app = Flask(__name__)
CORS(app)

# Configuration
API_KEY = os.environ.get('API_KEY', 'your-secret-key')
RAPIDAPI_KEY_HEADER = 'X-RapidAPI-Key'

# Simple API key validation
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get(RAPIDAPI_KEY_HEADER)
        
        if not api_key or api_key != API_KEY:
            return jsonify({
                'error': 'Invalid API key',
                'message': 'Please provide a valid RapidAPI key'
            }), 401
        return f(*args, **kwargs)
    return decorated

# Cache to reduce API calls
weather_cache = {}
CACHE_DURATION = 300  # 5 minutes in seconds

class WeatherService:
    """Weather API Service using Open-Meteo (No API key required!)"""
    
    @staticmethod
    def get_coordinates(city_name, country=None):
        """Geocode city name to coordinates using Open-Meteo Geocoding API"""
        
        # Check cache first
        cache_key = f"geocode_{city_name}_{country}"
        if cache_key in weather_cache:
            cache_time, cached_data = weather_cache[cache_key]
            if time.time() - cache_time < CACHE_DURATION:
                return cached_data
        
        try:
            # Build search query
            search_query = city_name
            if country:
                search_query = f"{city_name},{country}"
            
            # Call Open-Meteo Geocoding API
            response = requests.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={
                    "name": city_name,
                    "count": 1,
                    "language": "en",
                    "format": "json"
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get('results', [])
                
                if results:
                    result = results[0]
                    coordinates = {
                        'latitude': result['latitude'],
                        'longitude': result['longitude'],
                        'name': result['name'],
                        'country': result.get('country', ''),
                        'admin1': result.get('admin1', '')  # State/Region
                    }
                    
                    # Cache the result
                    weather_cache[cache_key] = (time.time(), coordinates)
                    return coordinates
                    
            return None
            
        except Exception as e:
            print(f"Geocoding error: {e}")
            return None
    
    @staticmethod
    def get_current_weather(city, country=None):
        """Get current weather using Open-Meteo API"""
        
        # Get coordinates
        coords = WeatherService.get_coordinates(city, country)
        if not coords:
            return {
                'error': 'City not found',
                'message': f'Could not find coordinates for {city}'
            }
        
        # Check cache
        cache_key = f"current_{coords['latitude']}_{coords['longitude']}"
        if cache_key in weather_cache:
            cache_time, cached_data = weather_cache[cache_key]
            if time.time() - cache_time < CACHE_DURATION:
                return cached_data
        
        try:
            # Call Open-Meteo Current Weather API
            response = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "current_weather": True,
                    "hourly": "temperature_2m,relativehumidity_2m,precipitation_probability",
                    "temperature_unit": "celsius",
                    "windspeed_unit": "kmh",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                current = data.get('current_weather', {})
                
                # Get additional data from hourly if needed
                hourly = data.get('hourly', {})
                current_hour = datetime.now().hour
                
                humidity = None
                if hourly and 'relativehumidity_2m' in hourly and len(hourly['relativehumidity_2m']) > current_hour:
                    humidity = hourly['relativehumidity_2m'][current_hour]
                
                # Map WMO weather codes to descriptions
                weather_codes = {
                    0: "Clear sky",
                    1: "Mainly clear",
                    2: "Partly cloudy",
                    3: "Overcast",
                    45: "Fog",
                    48: "Depositing rime fog",
                    51: "Light drizzle",
                    53: "Moderate drizzle",
                    55: "Dense drizzle",
                    56: "Light freezing drizzle",
                    57: "Dense freezing drizzle",
                    61: "Slight rain",
                    63: "Moderate rain",
                    65: "Heavy rain",
                    66: "Light freezing rain",
                    67: "Heavy freezing rain",
                    71: "Slight snow fall",
                    73: "Moderate snow fall",
                    75: "Heavy snow fall",
                    77: "Snow grains",
                    80: "Slight rain showers",
                    81: "Moderate rain showers",
                    82: "Violent rain showers",
                    85: "Slight snow showers",
                    86: "Heavy snow showers",
                    95: "Thunderstorm",
                    96: "Thunderstorm with slight hail",
                    99: "Thunderstorm with heavy hail"
                }
                
                weather_code = current.get('weathercode', 0)
                weather_description = weather_codes.get(weather_code, "Unknown")
                
                weather_data = {
                    'city': coords['name'],
                    'country': coords['country'],
                    'region': coords.get('admin1', ''),
                    'latitude': coords['latitude'],
                    'longitude': coords['longitude'],
                    'temperature': current.get('temperature'),
                    'wind_speed': current.get('windspeed'),
                    'wind_direction': current.get('winddirection'),
                    'weather_code': weather_code,
                    'weather': weather_description,
                    'humidity': humidity,
                    'timestamp': current.get('time', datetime.now().isoformat()),
                    'source': 'Open-Meteo'
                }
                
                # Cache the result
                weather_cache[cache_key] = (time.time(), weather_data)
                return weather_data
            else:
                return {
                    'error': 'API Error',
                    'message': f'Open-Meteo API returned status {response.status_code}'
                }
                
        except requests.exceptions.Timeout:
            return {'error': 'Timeout', 'message': 'Weather service timeout'}
        except requests.exceptions.ConnectionError:
            return {'error': 'Connection Error', 'message': 'Could not connect to weather service'}
        except Exception as e:
            return {'error': 'Service Error', 'message': str(e)}
    
    @staticmethod
    def get_forecast(city, days=5):
        """Get weather forecast using Open-Meteo API"""
        
        # Get coordinates
        coords = WeatherService.get_coordinates(city)
        if not coords:
            return {'error': 'City not found', 'message': f'Could not find coordinates for {city}'}
        
        # Limit days to 16 (Open-Meteo max)
        days = min(days, 16)
        
        # Check cache
        cache_key = f"forecast_{coords['latitude']}_{coords['longitude']}_{days}"
        if cache_key in weather_cache:
            cache_time, cached_data = weather_cache[cache_key]
            if time.time() - cache_time < CACHE_DURATION:
                return cached_data
        
        try:
            # Call Open-Meteo Forecast API
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
            
            if response.status_code == 200:
                data = response.json()
                daily = data.get('daily', {})
                
                # Map WMO weather codes to conditions
                def get_weather_condition(code):
                    conditions = {
                        0: "Sunny",
                        1: "Mostly Sunny",
                        2: "Partly Cloudy",
                        3: "Cloudy",
                        45: "Foggy",
                        48: "Foggy",
                        51: "Light Rain",
                        53: "Rain",
                        55: "Heavy Rain",
                        61: "Light Rain",
                        63: "Rain",
                        65: "Heavy Rain",
                        71: "Light Snow",
                        73: "Snow",
                        75: "Heavy Snow",
                        80: "Rain Showers",
                        81: "Rain Showers",
                        82: "Heavy Rain",
                        95: "Thunderstorm",
                        96: "Thunderstorm",
                        99: "Thunderstorm"
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
                        'weather_code': weather_code,
                        'condition': get_weather_condition(weather_code)
                    })
                
                forecast_data = {
                    'city': coords['name'],
                    'country': coords['country'],
                    'region': coords.get('admin1', ''),
                    'latitude': coords['latitude'],
                    'longitude': coords['longitude'],
                    'forecast': forecast,
                    'unit': 'celsius',
                    'source': 'Open-Meteo'
                }
                
                # Cache the result
                weather_cache[cache_key] = (time.time(), forecast_data)
                return forecast_data
            else:
                return {'error': 'API Error', 'message': f'Open-Meteo API returned status {response.status_code}'}
                
        except Exception as e:
            return {'error': 'Service Error', 'message': str(e)}
    
    @staticmethod
    def get_historical_weather(city, date):
        """Get historical weather data using Open-Meteo"""
        
        # Get coordinates
        coords = WeatherService.get_coordinates(city)
        if not coords:
            return {'error': 'City not found', 'message': f'Could not find coordinates for {city}'}
        
        try:
            # Parse date
            if isinstance(date, str):
                target_date = datetime.strptime(date, '%Y-%m-%d')
            else:
                target_date = date
            
            # Call Open-Meteo Historical API
            response = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={
                    "latitude": coords['latitude'],
                    "longitude": coords['longitude'],
                    "start_date": target_date.strftime('%Y-%m-%d'),
                    "end_date": target_date.strftime('%Y-%m-%d'),
                    "daily": "temperature_2m_mean,precipitation_sum,weathercode",
                    "temperature_unit": "celsius",
                    "timezone": "auto"
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                daily = data.get('daily', {})
                
                if daily and len(daily.get('time', [])) > 0:
                    historical_data = {
                        'city': coords['name'],
                        'country': coords['country'],
                        'date': daily['time'][0],
                        'temperature': daily['temperature_2m_mean'][0] if daily.get('temperature_2m_mean') else None,
                        'precipitation': daily['precipitation_sum'][0] if daily.get('precipitation_sum') else 0,
                        'weather_code': daily['weathercode'][0] if daily.get('weathercode') else None,
                        'source': 'Open-Meteo Archive'
                    }
                    return historical_data
            
            return {'error': 'Historical data not available', 'message': f'No historical data found for {date}'}
            
        except Exception as e:
            return {'error': 'Service Error', 'message': str(e)}

# API Endpoints

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        'name': 'Weather Insights API',
        'version': '2.0.0',
        'description': 'Comprehensive weather data API powered by Open-Meteo',
        'features': [
            'Real-time current weather',
            'Up to 16-day forecast',
            'Historical weather data',
            'No API key required for data source'
        ],
        'endpoints': [
            '/api/weather/current',
            '/api/weather/forecast',
            '/api/weather/history',
            '/api/health'
        ],
        'documentation': 'https://rapidapi.com/your-username/weather-insights-api'
    })

@app.route('/api/health', methods=['GET'])
def health_check():
    # Test Open-Meteo connection
    open_meteo_status = 'healthy'
    try:
        response = requests.get("https://api.open-meteo.com/v1/forecast", 
                              params={"latitude": 40.71, "longitude": -74.01, "current_weather": True},
                              timeout=5)
        if response.status_code != 200:
            open_meteo_status = 'degraded'
    except:
        open_meteo_status = 'unavailable'
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '2.0.0',
        'dependencies': {
            'open-meteo': open_meteo_status
        }
    })

@app.route('/api/weather/current', methods=['GET'])
@require_api_key
def get_current_weather():
    """Get current weather for a city"""
    
    city = request.args.get('city')
    country = request.args.get('country')
    units = request.args.get('units', 'metric')
    
    if not city:
        return jsonify({
            'error': 'Missing required parameter',
            'message': 'Please provide city name'
        }), 400
    
    try:
        weather_data = WeatherService.get_current_weather(city, country)
        
        # Check if there was an error
        if 'error' in weather_data:
            return jsonify({
                'success': False,
                'error': weather_data.get('error'),
                'message': weather_data.get('message')
            }), 404
        
        response = {
            'success': True,
            'data': weather_data,
            'units': units,
            'timestamp': datetime.now().isoformat()
        }
        
        return jsonify(response)
    
    except Exception as e:
        return jsonify({
            'error': 'Service error',
            'message': str(e)
        }), 500

@app.route('/api/weather/forecast', methods=['GET'])
@require_api_key
def get_weather_forecast():
    """Get weather forecast for a city (up to 16 days)"""
    
    city = request.args.get('city')
    days = int(request.args.get('days', 5))
    
    if not city:
        return jsonify({'error': 'City name required'}), 400
    
    if days > 16:
        return jsonify({'error': 'Maximum forecast days is 16'}), 400
    
    try:
        forecast_data = WeatherService.get_forecast(city, days)
        
        if 'error' in forecast_data:
            return jsonify({
                'success': False,
                'error': forecast_data.get('error'),
                'message': forecast_data.get('message')
            }), 404
        
        return jsonify({
            'success': True,
            'data': forecast_data,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/weather/history', methods=['GET'])
@require_api_key
def get_weather_history():
    """Get historical weather data for a specific date"""
    
    city = request.args.get('city')
    date = request.args.get('date')
    
    if not city:
        return jsonify({'error': 'City name required'}), 400
    
    if not date:
        return jsonify({'error': 'Date required (format: YYYY-MM-DD)'}), 400
    
    # Validate date format
    try:
        datetime.strptime(date, '%Y-%m-%d')
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
    
    try:
        historical_data = WeatherService.get_historical_weather(city, date)
        
        if 'error' in historical_data:
            return jsonify({
                'success': False,
                'error': historical_data.get('error'),
                'message': historical_data.get('message')
            }), 404
        
        return jsonify({
            'success': True,
            'data': historical_data,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/weather/bulk', methods=['POST'])
@require_api_key
def bulk_weather_request():
    """Get weather for multiple cities in one request"""
    
    data = request.get_json()
    cities = data.get('cities', [])
    
    if not cities:
        return jsonify({'error': 'Please provide list of cities'}), 400
    
    if len(cities) > 50:
        return jsonify({'error': 'Maximum 50 cities per bulk request'}), 400
    
    results = []
    errors = []
    
    for city in cities:
        weather = WeatherService.get_current_weather(city)
        if 'error' in weather:
            errors.append({'city': city, 'error': weather.get('message')})
        else:
            results.append(weather)
    
    return jsonify({
        'success': True,
        'data': results,
        'errors': errors if errors else None,
        'total': len(results),
        'failed': len(errors),
        'timestamp': datetime.now().isoformat()
    })

# Rate limiting implementation
rate_limits = {}

def rate_limit(limit_per_minute=60):
    """Rate limiting decorator"""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            api_key = request.headers.get(RAPIDAPI_KEY_HEADER)
            current_minute = datetime.now().strftime('%Y-%m-%d %H:%M')
            key = f"{api_key}:{current_minute}"
            
            if key in rate_limits:
                rate_limits[key] += 1
                if rate_limits[key] > limit_per_minute:
                    return jsonify({
                        'error': 'Rate limit exceeded',
                        'message': f'Maximum {limit_per_minute} requests per minute'
                    }), 429
            else:
                rate_limits[key] = 1
            
            # Clean old rate limit entries
            for old_key in list(rate_limits.keys()):
                if not old_key.endswith(current_minute):
                    del rate_limits[old_key]
            
            return f(*args, **kwargs)
        return decorated
    return decorator

# Apply rate limiting to endpoints
@app.route('/api/weather/current', methods=['GET'])
@rate_limit(60)
@require_api_key
def get_current_weather_with_limit():
    return get_current_weather()

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({
        'error': 'Endpoint not found',
        'message': 'Please check the API documentation'
    }), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        'error': 'Internal server error',
        'message': 'Please try again later'
    }), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
import os
import logging
import json
import time
import random  # Needed for forecast randomization
from datetime import datetime, timedelta

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import pandas as pd

import backend.data_processing as data_processing
import backend.api_utils as api_utils
import backend.health_utils as health_utils
import backend.external_api as external_api  # New import for external API
from backend.models import User
from backend.db_config import db, init_db

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Create the Flask app
app = Flask(__name__, static_folder='frontend/static', template_folder='frontend')

# Enable CORS for all routes
CORS(app, resources={r"/*": {"origins": "*"}})

# Configure Jinja2 to handle template rendering
app.jinja_env.auto_reload = True
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Initialize the database
init_db(app)

# Load data files
try:
    # Load marker data for the map
    with open('frontend/static/data/marker_data.json', 'r') as f:
        marker_data = json.load(f)

    # Load countries data
    with open('frontend/static/data/countries_data.json', 'r') as f:
        countries_data = json.load(f)

    # Load cities monthly data
    with open('frontend/static/data/cities_monthly_data.json', 'r') as f:
        cities_monthly_data = json.load(f)

    logger.info("Successfully loaded data files")
except Exception as e:
    logger.error(f"Error loading data files: {e}")
    marker_data = []
    countries_data = []
    cities_monthly_data = []

# Routes for serving frontend pages with template rendering
from flask import render_template

@app.route('/')
def index():
    # Calculate global average AQI with error handling
    valid_aqi_values = [c.get('2024 Avg', 0) for c in countries_data if c.get('2024 Avg') is not None and isinstance(c.get('2024 Avg'), (int, float))]
    global_avg_aqi = sum(valid_aqi_values) / len(valid_aqi_values) if valid_aqi_values else 45  # Default value if no valid data

    # Filter out countries with invalid AQI values
    valid_countries = [c for c in countries_data if c.get('2024 Avg') is not None and isinstance(c.get('2024 Avg'), (int, float)) and c.get('Country') is not None]
    
    # Get top 10 most polluted countries with valid data
    top_polluted_countries = sorted(valid_countries, key=lambda x: x.get('2024 Avg', 0), reverse=True)[:10]
    
    # Count countries with monitoring
    monitored_countries_count = len(set([c.get('Country') for c in valid_countries if c.get('Country')]))
    
    return render_template('index.html', 
                           global_avg_aqi=global_avg_aqi,
                           top_polluted_countries=top_polluted_countries,
                           monitored_countries_count=monitored_countries_count)

@app.route('/forecasting')
def forecasting_page():
    # Get list of cities for the datalist
    city_list = []
    try:
        with open('frontend/static/data/cities_list.json', 'r') as f:
            city_list = json.load(f)
    except Exception as e:
        logger.error(f"Error loading cities list: {e}")
    
    return render_template('forecasting.html', cities=city_list)

@app.route('/cities_comparison')
def cities_comparison_page():
    """
    Renders the cities comparison page with data from the CSV file
    """
    try:
        # Use pandas to read and process the CSV
        df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
        
        # Process each row to get cities with states for the dropdown
        cities_with_states = []
        for _, row in df.iterrows():
            city_name = row.get('City', '')
            state_name = row.get('State', '')
            
            if city_name and state_name:
                cities_with_states.append({
                    'city': city_name,
                    'state': state_name,
                    'display': f"{city_name}, {state_name}"
                })
                
        # Get list of countries for country comparison from world_most_polluted_countries_.csv
        try:
            countries_df = pd.read_csv('frontend/static/data/world_most_polluted_countries_.csv')
            countries = countries_df['Country'].tolist()
            # Sort countries alphabetically for easier selection
            countries.sort()
            logger.info(f"Loaded {len(countries)} countries from world_most_polluted_countries_.csv")
        except Exception as e:
            logger.error(f"Error loading countries data: {e}")
            # Fallback to a few common countries if CSV loading fails
            countries = ['India', 'China', 'United States', 'Bangladesh', 'Pakistan']
        
        # Pass the data to the template
        return render_template('cities_comparison.html', 
                              cities_with_states=cities_with_states,
                              countries=countries)
                              
    except Exception as e:
        logger.error(f"Error loading cities comparison page: {e}")
        # Return an empty list if there's an error
        return render_template('cities_comparison.html', 
                              cities_with_states=[],
                              countries=[])

@app.route('/interactive_map')
def interactive_map_page():
    return render_template('interactive_map.html')

@app.route('/map')
def map_page():
    return render_template('map.html')

@app.route('/about')
def about_page():
    return render_template('about.html')

@app.route('/impact_analysis')
def impact_analysis_page():
    return render_template('impact_analysis.html')

@app.route('/live_aqi')
def live_aqi_page():
    return render_template('live_aqi.html')

@app.route('/most_polluted')
def most_polluted_page():
    """
    Renders the most polluted countries and cities page
    Fetches data from world_most_polluted_countries_2024.csv and enhances with real-time API data
    """
    try:
        # First, try to generate enhanced data with real-time API integration
        # This will run in the background and not block the page load if it takes too long
        try:
            # Check if we need to refresh the enhanced data (once per day)
            enhanced_file = 'frontend/static/data/enhanced_country_data.json'
            should_refresh = False
            
            if os.path.exists(enhanced_file):
                # Check file age
                file_time = os.path.getmtime(enhanced_file)
                current_time = time.time()
                # Refresh if file is older than 1 day (86400 seconds)
                if current_time - file_time > 86400:
                    should_refresh = True
            else:
                should_refresh = True
                
            if should_refresh:
                # Run in a separate thread to avoid blocking the page load
                import threading
                thread = threading.Thread(target=external_api.export_enhanced_data)
                thread.daemon = True
                thread.start()
                logger.info("Started background task to refresh enhanced data")
        except Exception as e:
            logger.error(f"Error starting background task for enhanced data: {e}")
        
        # Load countries data from world_most_polluted_countries_2024.csv
        countries_df = pd.read_csv('frontend/static/data/world_most_polluted_countries_2024.csv')
        
        # Try to load enhanced data (if available)
        enhanced_data = {}
        try:
            if os.path.exists('frontend/static/data/enhanced_country_data.json'):
                with open('frontend/static/data/enhanced_country_data.json', 'r') as f:
                    enhanced_data_list = json.load(f)
                    # Convert to dictionary for easier lookup
                    enhanced_data = {item['Country']: item for item in enhanced_data_list if 'Country' in item}
                    logger.info(f"Loaded enhanced data for {len(enhanced_data)} countries")
        except Exception as e:
            logger.error(f"Error loading enhanced data: {e}")
        
        # Check if real-time data is available in the cache
        realtime_data = {}
        try:
            cache_file = 'frontend/static/data/cache/realtime_countries_data.json'
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cached = json.load(f)
                    if 'data' in cached:
                        realtime_data = cached['data']
                        logger.info(f"Loaded real-time data for {len(realtime_data)} countries")
        except Exception as e:
            logger.error(f"Error loading real-time data: {e}")
        
        # Convert to list of dictionaries for easier use in template
        countries_data = []
        for _, row in countries_df.iterrows():
            country_name = row.get('Country', '')
            country_data = {
                'Rank': row.get('Rank', ''),
                'Country': country_name,
                '2024 Avg': row.get('2024 Avg', '')
            }
            
            # Add monthly data
            months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            for month in months:
                country_data[month] = row.get(month, '')
            
            # Add real-time AQI if available
            if country_name in realtime_data:
                country_data['Realtime_AQI'] = realtime_data[country_name].get('aqi')
                country_data['Realtime_Timestamp'] = realtime_data[country_name].get('timestamp')
            elif enhanced_data and country_name in enhanced_data and enhanced_data[country_name].get('Realtime_AQI'):
                country_data['Realtime_AQI'] = enhanced_data[country_name].get('Realtime_AQI')
                country_data['Realtime_Timestamp'] = enhanced_data[country_name].get('Realtime_Timestamp')
            
            countries_data.append(country_data)
        
        # Sort by 2024 Avg (highest first)
        countries_data.sort(key=lambda x: float(x['2024 Avg']) if isinstance(x['2024 Avg'], (int, float)) else 0, reverse=True)
        
        # Calculate AQI category distribution
        categories = {
            'Good (0-50)': 0,
            'Moderate (51-100)': 0,
            'Unhealthy for Sensitive Groups (101-150)': 0,
            'Unhealthy (151-200)': 0,
            'Very Unhealthy (201-300)': 0,
            'Hazardous (301+)': 0
        }
        
        for country in countries_data:
            aqi = float(country['2024 Avg']) if isinstance(country['2024 Avg'], (int, float)) else 0
            
            if aqi <= 50:
                categories['Good (0-50)'] += 1
            elif aqi <= 100:
                categories['Moderate (51-100)'] += 1
            elif aqi <= 150:
                categories['Unhealthy for Sensitive Groups (101-150)'] += 1
            elif aqi <= 200:
                categories['Unhealthy (151-200)'] += 1
            elif aqi <= 300:
                categories['Very Unhealthy (201-300)'] += 1
            else:
                categories['Hazardous (301+)'] += 1
        
        # Also load cities data from the updated_polluted_cities_monthly_with_states.csv
        cities_df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
        
        # Process cities from updated_polluted_cities_monthly_with_states.csv
        cities_data = []
        for _, row in cities_df.iterrows():
            city_name = row.get('City', '')
            state_name = row.get('State', '')
            
            if not city_name or not state_name:
                continue
                
            # Calculate average AQI from monthly values
            months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
            monthly_values = []
            city_data = {
                'City': city_name,
                'State': state_name,
                'Country': row.get('Country', 'India'),  # Default to India if not specified
            }
            
            for month in months:
                value = row.get(month)
                city_data[month] = value  # Store raw value for template
                
                if value != '--' and not pd.isna(value):
                    try:
                        monthly_values.append(float(value))
                    except (ValueError, TypeError):
                        pass
            
            if monthly_values:
                avg_aqi = sum(monthly_values) / len(monthly_values)
                city_data['2024 Avg'] = round(avg_aqi, 1)
                cities_data.append(city_data)
                
        # We'll only use cities from updated_polluted_cities_monthly_with_states.csv as requested
        
        # Ensure we have city data loaded
        if not cities_data:
            logger.warning("No cities data loaded from updated_polluted_cities_monthly_with_states.csv, trying again...")
            try:
                # Try loading again with explicit file path
                cities_file_path = os.path.join(os.path.dirname(__file__), 'frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
                if os.path.exists(cities_file_path):
                    cities_df = pd.read_csv(cities_file_path)
                    
                    # Process cities data
                    for _, row in cities_df.iterrows():
                        city_name = row.get('City', '')
                        state_name = row.get('State', '')
                        
                        if not city_name or not state_name:
                            continue
                            
                        # Calculate average AQI from monthly values
                        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                        monthly_values = []
                        city_data = {
                            'City': city_name,
                            'State': state_name,
                            'Country': row.get('Country', 'India'),  # Default to India if not specified
                        }
                        
                        for month in months:
                            value = row.get(month)
                            city_data[month] = value  # Store raw value for template
                            
                            if value != '--' and not pd.isna(value):
                                try:
                                    monthly_values.append(float(value))
                                except (ValueError, TypeError):
                                    pass
                        
                        if monthly_values:
                            avg_aqi = sum(monthly_values) / len(monthly_values)
                            city_data['2024 Avg'] = round(avg_aqi, 1)
                            cities_data.append(city_data)
                    
                    logger.info(f"Successfully loaded {len(cities_data)} cities on second attempt")
                else:
                    logger.error(f"File not found: {cities_file_path}")
            except Exception as e:
                logger.error(f"Error reloading cities data: {e}")
        
        # Sort cities by AQI (highest first)
        cities_data.sort(key=lambda x: float(x.get('2024 Avg', 0)) if isinstance(x.get('2024 Avg'), (int, float)) else 0, reverse=True)
        
        # Get global summary
        global_summary = external_api.get_global_air_quality_summary()
        
        logger.info(f"Loaded {len(countries_data)} countries and {len(cities_data)} cities for most_polluted page")
        
        # Use only cities_data (from updated_polluted_cities_monthly_with_states.csv)
        all_cities = cities_data
        
        return render_template('most_polluted.html', 
                               top_polluted_countries=countries_data[:50],  # Limit to top 50
                               cities_with_states=all_cities[:100],         # Limit to top 100 cities
                               categories=categories,
                               global_summary=global_summary,
                               has_realtime_data=bool(realtime_data))
    except Exception as e:
        logger.error(f"Error loading most_polluted page: {e}")
        return render_template('most_polluted.html')

# Health recommendations page removed as requested

@app.route('/polluted_countries_ranking')
def polluted_countries_ranking_page():
    """
    Renders the top 50 most polluted countries ranking page
    Shows bar graph and detailed table using data from world_most_polluted_countries_.csv
    """
    return render_template('polluted_countries_ranking.html')

@app.route('/heatmap')
def heatmap_page():
    return render_template('heatmap.html')

# Serve static files
@app.route('/<path:path>')
def serve_frontend(path):
    if path.startswith('static/'):
        # Extract the part after 'static/' and serve from the static folder
        static_path = path[len('static/'):]
        return send_from_directory('frontend/static', static_path)
    elif os.path.exists(f'frontend/{path}'):
        return send_from_directory('frontend', path)
    else:
        # Default fallback - return to home page with all variables
        # Calculate global average AQI with error handling
        valid_aqi_values = [c.get('2024 Avg', 0) for c in countries_data if c.get('2024 Avg') is not None and isinstance(c.get('2024 Avg'), (int, float))]
        global_avg_aqi = sum(valid_aqi_values) / len(valid_aqi_values) if valid_aqi_values else 45  # Default value if no valid data
        
        # Filter out countries with invalid AQI values
        valid_countries = [c for c in countries_data if c.get('2024 Avg') is not None and isinstance(c.get('2024 Avg'), (int, float)) and c.get('Country') is not None]
        
        # Get top 10 most polluted countries with valid data
        top_polluted_countries = sorted(valid_countries, key=lambda x: x.get('2024 Avg', 0), reverse=True)[:10]
        
        # Count countries with monitoring
        monitored_countries_count = len(set([c.get('Country') for c in valid_countries if c.get('Country')]))
        
        return render_template('index.html', 
                             global_avg_aqi=global_avg_aqi,
                             top_polluted_countries=top_polluted_countries,
                             monitored_countries_count=monitored_countries_count)

# API routes
@app.route('/api')
def api_info():
    return jsonify({
        "name": "Air Quality Dashboard API",
        "version": "1.0.0",
        "description": "API endpoints for Air Quality Dashboard",
        "endpoints": [
            "/api/dashboard/data",
            "/api/map/data",
            "/api/most-polluted/data",
            "/api/aqi-csv-data",
            "/api/heatmap-data",
            "/api/india-aqi-data",
            "/api/global-air-quality-map-data",
            "/api/interactive-map-data",
            "/api/city-aqi/<city_name>",
            "/api/country-data/<country>",
            "/api/city-data/<city>/<state>",
            "/api/cities-for-comparison",
            "/api/city-comparison-data",
            "/api/cities/<country>",
            "/api/forecast",
            "/api/weather-correlation",
            "/api/seasonal-forecast",
            "/api/real-time-aqi",
            "/api/world-polluted-countries-2024"
        ]
    })

@app.route('/api/dashboard/data')
def dashboard_data():
    # Calculate global average AQI with error handling
    valid_aqi_values = [c.get('2024 Avg', 0) for c in countries_data if c.get('2024 Avg') is not None and isinstance(c.get('2024 Avg'), (int, float))]
    global_avg_aqi = sum(valid_aqi_values) / len(valid_aqi_values) if valid_aqi_values else 45  # Default value if no valid data

    # Filter out countries with invalid AQI values
    valid_countries = [c for c in countries_data if c.get('2024 Avg') is not None and isinstance(c.get('2024 Avg'), (int, float)) and c.get('Country') is not None]
    
    # Get top 10 most polluted countries with valid data
    top_polluted_countries = sorted(valid_countries, key=lambda x: x.get('2024 Avg', 0), reverse=True)[:10]
    
    # Count countries with monitoring
    monitored_countries_count = len(set([c.get('Country') for c in valid_countries if c.get('Country')]))

    return jsonify({
        'global_avg_aqi': global_avg_aqi,
        'top_polluted_countries': top_polluted_countries,
        'monitored_countries_count': monitored_countries_count,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/map/data')
def map_data():
    # Get unique continents, countries, and cities for filters
    continents = sorted([str(m.get('Continent', 'Unknown')) for m in marker_data if 'Continent' in m and m.get('Continent') is not None])
    continents = sorted(list(set(continents)))
    
    countries = [str(m.get('Country', 'Unknown')) for m in marker_data if 'Country' in m and m.get('Country') is not None]
    countries = sorted(list(set(countries)))
    
    cities = [str(m.get('City', 'Unknown')) for m in marker_data if 'City' in m and m.get('City') is not None]
    cities = sorted(list(set(cities)))

    return jsonify({
        'markers': marker_data[:100],  # Limit to 100 markers for performance
        'continents': continents,
        'countries': countries,
        'cities': cities[:500],  # Limit to 500 cities for performance
        'timestamp': datetime.now().isoformat()
    })

@app.route('/api/most-polluted/data')
def most_polluted_data():
    try:
        # Load countries data from world_most_polluted_countries_2024.csv
        countries_df = pd.read_csv('frontend/static/data/world_most_polluted_countries_2024.csv')
        
        # Convert to list of dictionaries
        countries_data_2024 = []
        for _, row in countries_df.iterrows():
            country_data = row.to_dict()
            countries_data_2024.append(country_data)
            
        # Try to enhance with real-time data
        try:
            real_time_data = {}
            cache_file = 'frontend/static/data/cache/realtime_countries_data.json'
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cached = json.load(f)
                    if 'data' in cached:
                        real_time_data = cached['data']
            
            # Add real-time data if available
            if real_time_data:
                for country in countries_data_2024:
                    country_name = country.get('Country', '')
                    if country_name in real_time_data:
                        country['Realtime_AQI'] = real_time_data[country_name].get('aqi')
                        country['Realtime_Timestamp'] = real_time_data[country_name].get('timestamp')
        except Exception as e:
            logger.error(f"Error enhancing with real-time data: {e}")
            
        # Sort by 2024 Avg (highest first)
        countries_data_2024.sort(key=lambda x: float(x.get('2024 Avg', 0)) if isinstance(x.get('2024 Avg'), (int, float)) else 0, reverse=True)
        
        # Load cities directly from updated_polluted_cities_monthly_with_states.csv
        cities_with_states = []
        try:
            cities_df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
            for _, row in cities_df.iterrows():
                city = row.get('City')
                state = row.get('State')
                if city and state:
                    cities_with_states.append({
                        'City': city,  # Use capital City/State to match your HTML
                        'State': state
                    })
            logger.info(f"Successfully loaded {len(cities_with_states)} cities for API from CSV")
        except Exception as e:
            logger.error(f"Error loading cities from CSV for API: {e}")
        
        return jsonify({
            'top_polluted_countries': countries_data_2024[:50],  # Limit to top 50
            'cities_with_states': cities_with_states[:100],  # Limit to 100 cities for performance
            'timestamp': datetime.now().isoformat(),
            'has_realtime_data': bool(real_time_data)
        })
    except Exception as e:
        logger.error(f"Error in most_polluted_data API: {e}")
        # Fallback to pre-loaded data
        top_polluted_countries = sorted(countries_data, key=lambda x: x.get('2024 Avg', 0), reverse=True)[:50]
        cities_with_states = []
        if cities_monthly_data:
            for city_data in cities_monthly_data:
                if 'City' in city_data and 'State' in city_data:
                    cities_with_states.append({
                        'city': city_data['City'],
                        'state': city_data['State']
                    })
        
        # Only use cities from updated_polluted_cities_monthly_with_states.csv as requested
        return jsonify({
            'error': str(e),
            'top_polluted_countries': top_polluted_countries,
            'cities_with_states': cities_with_states[:100],
            'timestamp': datetime.now().isoformat()
        })

@app.route('/api/real-time-aqi')
def real_time_aqi():
    """
    API endpoint to get real-time air quality data for countries
    Uses the IQAir API with the provided key
    """
    try:
        # Check if we have cached data
        cache_file = 'frontend/static/data/cache/realtime_countries_data.json'
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cached = json.load(f)
                if 'data' in cached:
                    return jsonify({
                        'status': 'success',
                        'data': cached['data'],
                        'timestamp': cached.get('timestamp', time.time()),
                        'cached': True
                    })
        
        # If no cached data, trigger a refresh
        countries_df = pd.read_csv('frontend/static/data/world_most_polluted_countries_2024.csv')
        countries = countries_df['Country'].tolist()
        
        # Get data for the first 5 countries (to avoid API rate limits)
        sample_countries = countries[:5] if len(countries) > 5 else countries
        real_time_data = external_api.fetch_realtime_data_for_countries(sample_countries, cache_duration=3600)
        
        return jsonify({
            'status': 'success',
            'data': real_time_data,
            'timestamp': time.time(),
            'cached': False
        })
    except Exception as e:
        logger.error(f"Error in real_time_aqi API: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        })
        
@app.route('/api/world-polluted-countries-2024')
def world_polluted_countries_2024():
    """
    API endpoint to get data from world_most_polluted_countries_2024.csv
    Enhanced with real-time data if available
    """
    try:
        # Load countries data from world_most_polluted_countries_2024.csv
        countries_df = pd.read_csv('frontend/static/data/world_most_polluted_countries_2024.csv')
        
        # Process into a format suitable for the frontend
        result = {
            'data': countries_df.to_dict('records'),
            'timestamp': datetime.now().isoformat()
        }
        
        # Try to enhance with real-time data
        try:
            # Generate enhanced data if needed
            enhanced_file = 'frontend/static/data/enhanced_country_data.json'
            if not os.path.exists(enhanced_file) or (time.time() - os.path.getmtime(enhanced_file)) > 86400:
                # Generate enhanced data in the background
                import threading
                thread = threading.Thread(target=external_api.export_enhanced_data)
                thread.daemon = True
                thread.start()
                logger.info("Started background task to generate enhanced data")
            
            # Read real-time data from cache if available
            real_time_data = {}
            cache_file = 'frontend/static/data/cache/realtime_countries_data.json'
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    cached = json.load(f)
                    if 'data' in cached:
                        real_time_data = cached['data']
                        result['has_realtime_data'] = True
                        result['realtime_data'] = real_time_data
                        result['realtime_timestamp'] = cached.get('timestamp')
        except Exception as e:
            logger.error(f"Error enhancing with real-time data in API: {e}")
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in world_polluted_countries_2024 API: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        })

@app.route('/api/aqi-csv-data')
def aqi_csv_data():
    """
    API endpoint to get processed AQI CSV data
    """
    try:
        # Path to the CSV file
        csv_file_path = 'frontend/static/data/AQI and Lat Long of Countries.csv'
        
        # Read the CSV file using pandas
        df = pd.read_csv(csv_file_path)
        
        # Group by country and calculate average AQI
        country_avg = df.groupby('Country')['AQI Value'].mean().reset_index()
        country_avg = country_avg.sort_values('AQI Value', ascending=False)
        
        # Get top 20 countries with highest AQI values
        top_countries = country_avg.head(20).to_dict('records')
        
        # Group by city and get top 20 cities with highest AQI values
        city_data = df.drop_duplicates(subset=['City', 'Country'])
        city_data = city_data.sort_values('AQI Value', ascending=False)
        top_cities = city_data.head(20)[['City', 'Country', 'AQI Value', 'AQI Category']].to_dict('records')
        
        # Get cleanest cities (lowest AQI)
        cleanest_cities = city_data.sort_values('AQI Value').head(10)[['City', 'Country', 'AQI Value', 'AQI Category']].to_dict('records')
        
        # Get data for the heatmap
        heatmap_data = df[['lat', 'lng', 'AQI Value', 'City', 'Country']].rename(
            columns={'lat': 'lat', 'lng': 'lng', 'AQI Value': 'value'}
        ).to_dict('records')
        
        # Calculate distribution by category
        category_counts = df['AQI Category'].value_counts().to_dict()
        
        return jsonify({
            'top_countries': top_countries,
            'top_cities': top_cities,
            'cleanest_cities': cleanest_cities,
            'heatmap_data': heatmap_data[:100],  # Limit to 100 items for performance
            'category_counts': category_counts,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Error processing CSV data: {e}")
        return jsonify({
            'error': str(e)
        }), 500

@app.route('/api/heatmap-data')
def heatmap_data():
    try:
        df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
        data = []
        for _, row in df.iterrows():
            monthly_avg = sum(float(row[month]) for month in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                                                            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] 
                            if str(row[month]) != '--') / 12
            data.append({
                'city': row['City'],
                'state': row['State'],
                'value': monthly_avg
            })
        return jsonify(data[:100])  # Limit to 100 items for performance
    except Exception as e:
        logger.error(f"Error getting heatmap data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/india-aqi-data')
def india_aqi_data():
    """
    API endpoint to get real-time air quality data for India
    """
    try:
        # Get air quality data for all stations in India
        india_data = api_utils.get_india_air_quality_map_data()
        return jsonify(india_data[:100])  # Limit to 100 items for performance
    except Exception as e:
        logger.error(f"Error getting India AQI data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/city-aqi/<city_name>')
def city_aqi(city_name):
    """
    API endpoint to get real-time air quality data for a specific city
    """
    try:
        # Get air quality data for the specified city
        city_data = api_utils.get_city_air_quality(city_name)
        if city_data:
            return jsonify(city_data)
        else:
            return jsonify({"error": f"No data found for city '{city_name}'"}), 404
    except Exception as e:
        logger.error(f"Error getting city AQI data for {city_name}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/global-air-quality-map-data')
def global_air_quality_map_data():
    """
    API endpoint to get global air quality data
    """
    try:
        # Get global air quality data
        global_data = api_utils.get_global_air_quality_map_data()
        return jsonify(global_data[:100])  # Limit to 100 items for performance
    except Exception as e:
        logger.error(f"Error getting global AQI data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/interactive-map-data')
def interactive_map_data():
    """
    API endpoint to get data for the interactive map with detailed markers
    Reads directly from frontend/static/data/AQI and Lat Long of Countries.csv
    """
    try:
        # Read data directly from the CSV file
        csv_file_path = 'frontend/static/data/AQI and Lat Long of Countries.csv'
        logger.info(f"Reading interactive map data from {csv_file_path}")
        
        # Read the CSV file using pandas
        df = pd.read_csv(csv_file_path)
        
        # Convert to list of dictionaries for the map markers
        markers = []
        for _, row in df.iterrows():
            try:
                # Convert values to appropriate types
                aqi_value = float(row['AQI Value']) if not pd.isna(row['AQI Value']) else 0
                
                # Generate popup content
                pollution_info = []
                for pollutant in ['CO AQI Value', 'Ozone AQI Value', 'NO2 AQI Value', 'PM2.5 AQI Value']:
                    if pollutant in row and not pd.isna(row[pollutant]):
                        name = pollutant.replace(' AQI Value', '')
                        category = row.get(pollutant.replace('Value', 'Category'), '')
                        pollution_info.append(f"<strong>{name}:</strong> {row[pollutant]} {category}")
                
                pollution_html = '<br>'.join(pollution_info) if pollution_info else "No detailed pollutant data available"
                
                popup_content = f"""
                <div class="marker-popup">
                    <h5>{row['City']}, {row['Country']}</h5>
                    <div class="aqi-badge" style="background-color: {api_utils.get_aqi_category_and_color(aqi_value)[1]}">
                        AQI: {int(aqi_value)}
                    </div>
                    <p>Category: {row['AQI Category']}</p>
                    <div class="pollutants-details">
                        {pollution_html}
                    </div>
                </div>
                """
                
                # Get color based on AQI value
                if aqi_value <= 50:
                    color = "#009966"  # Good
                elif aqi_value <= 100:
                    color = "#ffde33"  # Moderate
                elif aqi_value <= 150:
                    color = "#ff9933"  # Unhealthy for Sensitive Groups
                elif aqi_value <= 200:
                    color = "#cc0033"  # Unhealthy
                elif aqi_value <= 300:
                    color = "#660099"  # Very Unhealthy
                else:
                    color = "#7e0023"  # Hazardous
                
                # Create marker in the expected format for the frontend
                marker = {
                    'Latitude': float(row['lat']) if not pd.isna(row['lat']) else 0,
                    'Longitude': float(row['lng']) if not pd.isna(row['lng']) else 0,
                    'AQI': aqi_value,
                    'AQI_Category': row['AQI Category'] if not pd.isna(row['AQI Category']) else "Unknown",
                    'City': row['City'] if not pd.isna(row['City']) else "Unknown",
                    'Country': row['Country'] if not pd.isna(row['Country']) else "Unknown",
                    'Station': row.get('Station Name', f"{row['City']}, {row['Country']}"),
                    'color': color,
                    'popup_content': popup_content
                }
                
                # Add pollutant data if available
                for col in df.columns:
                    if col not in ['lat', 'lng', 'City', 'Country', 'AQI Value', 'AQI Category'] and not pd.isna(row[col]):
                        marker[col.replace(' ', '_')] = row[col]
                
                markers.append(marker)
            except (ValueError, KeyError, TypeError) as e:
                # Skip rows with invalid data
                logger.warning(f"Skipping row due to error: {e}")
                continue
        
        logger.info(f"Processed {len(markers)} markers from CSV file")
        
        # Save to cache for future use
        if len(markers) > 0:
            api_utils.save_map_data_to_cache(markers)
        
        # Return markers directly as an array (what the frontend expects)
        return jsonify(markers[:1000])  # Limit to 1000 markers for performance
    except Exception as e:
        logger.error(f"Error getting interactive map data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/country-data/<country>')
def country_data(country):
    try:
        country_data = None
        
        # Find country in the loaded data
        for c in countries_data:
            if c.get('Country') == country:
                country_data = c
                break
                
        if not country_data:
            return jsonify({"error": f"Country '{country}' not found"}), 404
            
        # Get monthly data for charting
        monthly_data = [country_data.get(month, 0) for month in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']]
        
        result = {
            "name": country_data.get('Country'),
            "rank": country_data.get('Rank', 0),
            "avg_aqi": country_data.get('2024 Avg', 0),
            "monthly_data": monthly_data,
            "months": ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        }
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting country data for {country}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/city-data/<path:city_state>')
def city_data(city_state):
    try:
        parts = city_state.split('/')
        if len(parts) != 2:
            return jsonify({"error": "City data must be requested as city/state"}), 400
            
        city = parts[0]
        state = parts[1]
        
        city_data = None
        
        logger.info(f"Looking for city data for {city}, {state}")
        
        # First try to find city in cities_monthly_data (from cache JSON)
        if cities_monthly_data:
            for c in cities_monthly_data:
                if c.get('City') == city and c.get('State') == state:
                    city_data = c
                    logger.info(f"Found city in cities_monthly_data: {city}")
                    break
                
        # If not found, try to find directly in CSV file
        if not city_data:
            try:
                cities_df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
                city_row = cities_df[(cities_df['City'] == city) & (cities_df['State'] == state)]
                
                if not city_row.empty:
                    # Convert to dictionary
                    city_data = city_row.iloc[0].to_dict()
                    logger.info(f"Found city in CSV: {city}")
            except Exception as e:
                logger.error(f"Error reading updated_polluted_cities_monthly_with_states.csv: {e}")
        
        if not city_data:
            return jsonify({"error": f"City '{city}, {state}' not found"}), 404
            
        # Get monthly data for charting, handling missing or non-numeric values
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        monthly_data = []
        monthly_categories = []  # AQI categories for each month
        
        for month in month_names:
            value = city_data.get(month)
            # Handle missing or non-numeric values
            if value == '--' or pd.isna(value):
                monthly_data.append(None)
                monthly_categories.append(None)
            else:
                try:
                    float_value = float(value)
                    monthly_data.append(float_value)
                    
                    # Add AQI category
                    if float_value <= 50:
                        monthly_categories.append("Good")
                    elif float_value <= 100:
                        monthly_categories.append("Moderate")
                    elif float_value <= 150:
                        monthly_categories.append("Unhealthy for Sensitive Groups")
                    elif float_value <= 200:
                        monthly_categories.append("Unhealthy")
                    elif float_value <= 300:
                        monthly_categories.append("Very Unhealthy")
                    else:
                        monthly_categories.append("Hazardous")
                        
                except (ValueError, TypeError):
                    monthly_data.append(None)
                    monthly_categories.append(None)
        
        # Calculate average AQI from valid monthly values
        valid_values = [v for v in monthly_data if v is not None]
        avg_aqi = round(sum(valid_values) / len(valid_values), 1) if valid_values else None
        
        # Calculate min and max values for reference lines
        min_value = min([v for v in valid_values if v is not None] or [0]) if valid_values else None
        max_value = max([v for v in valid_values if v is not None] or [0]) if valid_values else None
        
        # Add individual month data as separate properties too
        result = {
            "name": city_data.get('City'),
            "state": city_data.get('State'),
            "country": city_data.get('Country', 'India'),  # Default to India if not specified
            "avg_aqi": avg_aqi,
            "min_aqi": min_value,
            "max_aqi": max_value,
            "monthly_data": monthly_data,
            "monthly_categories": monthly_categories,
            "months": month_names
        }
        
        # Add individual month properties for compatibility
        for i, month in enumerate(month_names):
            result[month] = monthly_data[i]
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error getting city data for {city_state}: {e}")
        return jsonify({"error": str(e)}), 500

# Helper function to process city data from CSV
def process_city_data(row):
    """Helper function to process a city row from the CSV file"""
    try:
        city_name = row.get('City', '')
        state_name = row.get('State', '')
        
        if not city_name or not state_name:
            return None
            
        # Extract monthly AQI values
        monthly_values = []
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        full_month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
        
        for month in month_names:
            value = row.get(month)
            # Handle missing or non-numeric values
            if value == '--' or pd.isna(value):
                monthly_values.append(None)
            else:
                try:
                    monthly_values.append(float(value))
                except (ValueError, TypeError):
                    monthly_values.append(None)
        
        # Calculate average of available values
        valid_values = [v for v in monthly_values if v is not None]
        avg_aqi = round(sum(valid_values) / len(valid_values), 1) if valid_values else None
        
        # Find peak and cleanest months
        if valid_values:
            peak_value = max(valid_values)
            peak_month_index = monthly_values.index(peak_value)
            peak_month = full_month_names[peak_month_index]
            
            cleanest_value = min(valid_values)
            cleanest_month_index = monthly_values.index(cleanest_value)
            cleanest_month = full_month_names[cleanest_month_index]
        else:
            peak_value = None
            peak_month = "N/A"
            cleanest_value = None
            cleanest_month = "N/A"
        
        # Calculate seasonal averages
        # Winter: Dec, Jan, Feb
        winter_months = [monthly_values[0], monthly_values[1], monthly_values[11]] # Jan, Feb, Dec
        winter_avg = round(sum([v for v in winter_months if v is not None]) / len([v for v in winter_months if v is not None]), 1) if any(v is not None for v in winter_months) else "N/A"
        
        # Summer: Mar, Apr, May
        summer_months = [monthly_values[2], monthly_values[3], monthly_values[4]] # Mar, Apr, May
        summer_avg = round(sum([v for v in summer_months if v is not None]) / len([v for v in summer_months if v is not None]), 1) if any(v is not None for v in summer_months) else "N/A"
        
        # Monsoon: Jun, Jul, Aug, Sep
        monsoon_months = [monthly_values[5], monthly_values[6], monthly_values[7], monthly_values[8]] # Jun, Jul, Aug, Sep
        monsoon_avg = round(sum([v for v in monsoon_months if v is not None]) / len([v for v in monsoon_months if v is not None]), 1) if any(v is not None for v in monsoon_months) else "N/A"
        
        # Post-Monsoon: Oct, Nov
        post_monsoon_months = [monthly_values[9], monthly_values[10]] # Oct, Nov
        post_monsoon_avg = round(sum([v for v in post_monsoon_months if v is not None]) / len([v for v in post_monsoon_months if v is not None]), 1) if any(v is not None for v in post_monsoon_months) else "N/A"
        
        return {
            'city': city_name,
            'state': state_name,
            'display': f"{city_name}, {state_name}",
            'monthly_values': monthly_values,
            'monthly_data': monthly_values, # Duplicate for the chart
            'avg_aqi': avg_aqi,
            'peak_month': peak_month,
            'peak_value': peak_value,
            'cleanest_month': cleanest_month,
            'cleanest_value': cleanest_value,
            'seasonal_data': {
                'winter': winter_avg,
                'summer': summer_avg,
                'monsoon': monsoon_avg,
                'post_monsoon': post_monsoon_avg
            }
        }
    except Exception as e:
        logger.error(f"Error processing city data: {e}")
        return None

@app.route('/api/cities-for-comparison')
def cities_for_comparison():
    """
    API endpoint to get cities data for comparison charts
    Uses data from frontend/static/data/updated_polluted_cities_monthly_with_states.csv
    """
    try:
        # Use pandas to read and process the CSV
        df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
        
        # Process each row into the format we need
        cities_data = []
        for _, row in df.iterrows():
            city_data = process_city_data(row)
            if city_data:
                cities_data.append(city_data)
        
        logger.info(f"Successfully loaded {len(cities_data)} cities for comparison")
        return jsonify(cities_data)  # Return all cities for the dropdown
    except Exception as e:
        logger.error(f"Error getting cities for comparison: {e}")
        return jsonify([]), 500

@app.route('/api/city-comparison-data')
def city_comparison_data():
    """
    API endpoint to get detailed comparison data for two specific cities
    """
    city1 = request.args.get('city1')
    state1 = request.args.get('state1')
    city2 = request.args.get('city2')
    state2 = request.args.get('state2')
    
    if not city1 or not state1 or not city2 or not state2:
        return jsonify({'error': 'Both city1, state1, city2, and state2 parameters are required'}), 400
        
    try:
        logger.info(f"Comparing cities: {city1}, {state1} and {city2}, {state2}")
        
        # Use pandas to read and process the CSV
        df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
        
        # Find the two cities
        city1_data = df[(df['City'] == city1) & (df['State'] == state1)]
        city2_data = df[(df['City'] == city2) & (df['State'] == state2)]
        
        if city1_data.empty or city2_data.empty:
            return jsonify({'error': 'One or both cities not found'}), 404
            
        # Process each city's data
        city1_processed = process_city_data(city1_data.iloc[0])
        city2_processed = process_city_data(city2_data.iloc[0])
        
        # Get month labels
        months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
        
        result = {
            'city1': city1_processed,
            'city2': city2_processed,
            'months': months
        }
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error comparing cities: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/country-comparison-data')
def country_comparison_data():
    """
    API endpoint to get detailed comparison data for two specific countries
    Uses data from frontend/static/data/world_most_polluted_countries_.csv
    """
    country1 = request.args.get('country1')
    country2 = request.args.get('country2')
    
    if not country1 or not country2:
        return jsonify({'error': 'Both country1 and country2 parameters are required'}), 400
        
    try:
        logger.info(f"Comparing countries: {country1} and {country2}")
        
        # Use pandas to read and process the CSV
        df = pd.read_csv('frontend/static/data/world_most_polluted_countries_.csv')
        
        # Find the two countries
        country1_data = df[df['Country'] == country1]
        country2_data = df[df['Country'] == country2]
        
        if country1_data.empty or country2_data.empty:
            return jsonify({'error': 'One or both countries not found'}), 404
            
        # Process each country's data
        country1_processed = process_country_data(country1_data.iloc[0])
        country2_processed = process_country_data(country2_data.iloc[0])
        
        # Get month labels
        months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
        
        result = {
            'country1': country1_processed,
            'country2': country2_processed,
            'months': months
        }
        
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error comparing countries: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/cities/<country>')
@app.route('/api/cities-for-country/<country>')
def cities_for_country(country):
    """
    API endpoint to get cities for a specific country from our dataset
    """
    try:
        # Get cities from marker data
        cities = []
        
        # Case-insensitive country matching
        country_markers = [m for m in marker_data if str(m.get('Country', '')).lower() == country.lower()]
        
        # Extract unique city names
        for marker in country_markers:
            if 'City' in marker and marker['City'] not in cities:
                cities.append(marker['City'])
                
        # Also check cities_monthly_data for additional cities
        country_data = [item for item in cities_monthly_data 
                         if str(item.get('Country', '')).lower() == country.lower()]
        
        if country_data:
            for city_data in country_data:
                if 'City' in city_data and city_data['City'] not in cities:
                    cities.append(city_data['City'])
        
        # Sort cities alphabetically
        cities.sort()
        
        logger.info(f"Found {len(cities)} cities for country {country}")
        return jsonify(cities)
    except Exception as e:
        logger.error(f"Error getting cities for country {country}: {e}")
        return jsonify([]), 500

@app.route('/api/forecast')
def get_forecast():
    """
    API endpoint to get 7-day AQI forecast for a specific city
    Uses weather data from OpenWeatherMap API and statistical patterns
    """
    from backend.config import WEATHER_API_KEY
    import requests
    
    city = request.args.get('city')
    include_weather = request.args.get('include_weather', 'false').lower() == 'true'
    
    if not city:
        return jsonify({"error": "City parameter is required"}), 400
        
    try:
        # Get current AQI data for the city
        city_data = api_utils.get_city_air_quality(city)
        
        # If no direct city data, try to find the city in our datasets
        if not city_data:
            # Try finding in our CSV data
            try:
                df = pd.read_csv('frontend/static/data/updated_polluted_cities_monthly_with_states.csv')
                city_row = df[df['City'].str.lower() == city.lower()]
                
                if not city_row.empty:
                    # Calculate average AQI from monthly data
                    monthly_data = city_row.iloc[0]
                    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                    valid_months = [float(monthly_data[m]) for m in months if str(monthly_data[m]) != '--']
                    
                    if valid_months:
                        avg_aqi = sum(valid_months) / len(valid_months)
                        city_data = {
                            "city": city,
                            "aqi": int(avg_aqi),
                            "category": health_utils.get_aqi_category(avg_aqi),
                            "pollutants": {}
                        }
                    else:
                        return jsonify({"error": f"No valid AQI data found for city '{city}'"}), 404
                else:
                    return jsonify({"error": f"No data found for city '{city}'"}), 404
            except Exception as e:
                logger.error(f"Error finding city in datasets: {e}")
                return jsonify({"error": f"No data found for city '{city}'"}), 404
        
        # Get the current AQI value
        current_aqi = city_data.get('aqi', 50)
        
        # Try to get weather forecast data for the city if weather is included
        weather_data = None
        if include_weather:
            try:
                # Call OpenWeatherMap API for 7-day forecast
                weather_url = f"https://api.openweathermap.org/data/2.5/forecast?q={city}&appid={WEATHER_API_KEY}&units=metric"
                weather_response = requests.get(weather_url)
                if weather_response.status_code == 200:
                    weather_data = weather_response.json()
                    logger.info(f"Successfully obtained weather data for {city}")
                else:
                    logger.warning(f"Failed to get weather data for {city}: {weather_response.status_code}")
            except Exception as e:
                logger.error(f"Error fetching weather data: {e}")
        
        # Generate 7-day forecast
        forecast = []
        
        # Get date range for the next 7 days
        today = datetime.now()
        
        # Generate forecast based on current conditions
        # This is a simplified model that would be replaced with an actual ML model in production
        weather_impact = calculate_weather_impact(city_data.get('iaqi', {}))
        
        for i in range(7):
            forecast_date = today + timedelta(days=i)
            date_str = forecast_date.strftime("%Y-%m-%d")
            day_name = forecast_date.strftime("%A")
            
            # Generate a plausible AQI value based on current AQI with some variance
            # Add a slight trend based on the day of week (weekends typically have lower pollution)
            base_variance = random.uniform(-0.15, 0.15)  # -15% to +15%
            day_factor = -0.05 if forecast_date.weekday() >= 5 else 0.02  # Weekend vs weekday
            daily_aqi = max(1, int(current_aqi * (1 + base_variance + day_factor * i + weather_impact)))
            
            # Determine category based on AQI value
            category = health_utils.get_aqi_category(daily_aqi)
            
            # Get color code for the AQI value
            color = api_utils.get_aqi_category_and_color(daily_aqi)[1]
            
            # Add weather information if requested
            day_data = {
                "date": date_str,
                "day_name": day_name,
                "aqi": daily_aqi,
                "category": category,
                "color": color
            }
            
            if include_weather:
                # Use real weather data if available, otherwise generate plausible data
                if weather_data and 'list' in weather_data:
                    # Try to find forecast data for the specific day
                    day_forecast = None
                    for forecast_item in weather_data['list']:
                        forecast_timestamp = datetime.fromtimestamp(forecast_item['dt'])
                        if forecast_timestamp.date() == forecast_date.date():
                            day_forecast = forecast_item
                            break
                    
                    if day_forecast:
                        # Extract weather data from the forecast
                        weather_main = day_forecast['weather'][0]['main'].lower()
                        weather_desc = day_forecast['weather'][0]['description'].lower()
                        temp = day_forecast['main']['temp']
                        humidity = day_forecast['main']['humidity']
                        
                        # Map OpenWeatherMap condition to our condition categories
                        if 'thunderstorm' in weather_main:
                            condition = 'thunderstorm'
                        elif 'rain' in weather_main or 'drizzle' in weather_main:
                            condition = 'rain'
                        elif 'snow' in weather_main:
                            condition = 'snow'
                        elif 'fog' in weather_desc or 'mist' in weather_desc:
                            condition = 'fog'
                        elif 'cloud' in weather_main:
                            condition = 'cloudy' if 'overcast' in weather_desc else 'partly cloudy'
                        else:
                            condition = 'sunny'
                        
                        day_data["weather"] = {
                            "conditions": condition,
                            "temp": round(temp, 1),
                            "humidity": humidity,
                            "wind_speed": day_forecast['wind']['speed'] if 'wind' in day_forecast else 0,
                            "source": "OpenWeatherMap"
                        }
                    else:
                        # Fall back to generated data if no specific day forecast is found
                        _generate_weather_data(day_data, today, forecast_date)
                else:
                    # Fall back to generated data if no weather data is available
                    _generate_weather_data(day_data, today, forecast_date)
                
            forecast.append(day_data)
        
        return jsonify({
            "city": city,
            "current_aqi": current_aqi,
            "forecast": forecast
        })
    except Exception as e:
        logger.error(f"Error generating forecast for {city}: {e}")
        return jsonify({"error": str(e)}), 500
        
def _generate_weather_data(day_data, today, forecast_date):
    """Generate plausible weather data for a given day"""
    seasons = ["winter", "spring", "summer", "fall"]
    current_month = today.month
    season_idx = (current_month % 12) // 3
    season = seasons[season_idx]
    
    weather_conditions = [
        "sunny", "partly cloudy", "cloudy", "rain", "thunderstorm", "fog"
    ]
    
    # Weighted probabilities based on season
    if season == "winter":
        weights = [0.3, 0.2, 0.2, 0.1, 0.0, 0.2]
    elif season == "spring":
        weights = [0.3, 0.3, 0.2, 0.15, 0.05, 0.0]
    elif season == "summer":
        weights = [0.5, 0.2, 0.1, 0.1, 0.1, 0.0]
    else:  # fall
        weights = [0.3, 0.3, 0.2, 0.1, 0.0, 0.1]
        
    # Select condition based on weights
    condition = random.choices(weather_conditions, weights=weights, k=1)[0]
    
    # Generate temperature based on season
    if season == "winter":
        temp = round(random.uniform(-5, 10), 1)
    elif season == "spring":
        temp = round(random.uniform(10, 25), 1)
    elif season == "summer":
        temp = round(random.uniform(20, 35), 1)
    else:  # fall
        temp = round(random.uniform(5, 20), 1)
        
    # Generate humidity based on condition
    if condition in ["rain", "thunderstorm"]:
        humidity = random.randint(70, 95)
    elif condition == "fog":
        humidity = random.randint(80, 100)
    elif condition == "cloudy":
        humidity = random.randint(50, 80)
    else:  # sunny or partly cloudy
        humidity = random.randint(30, 60)
    
    # Generate wind speed
    if condition in ["thunderstorm", "rain"]:
        wind_speed = round(random.uniform(10, 30), 1)
    elif condition == "sunny":
        wind_speed = round(random.uniform(5, 15), 1)
    else:
        wind_speed = round(random.uniform(2, 10), 1)
        
    day_data["weather"] = {
        "conditions": condition,
        "temp": temp,
        "humidity": humidity,
        "wind_speed": wind_speed,
        "source": "Generated"
    }

def process_country_data(row):
    """Helper function to process a country row from the CSV file"""
    try:
        country_name = row.get('Country', '')
        
        if not country_name:
            return None
            
        # Extract monthly AQI values
        monthly_values = []
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        full_month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
        
        for month in month_names:
            value = row.get(month)
            # Handle missing or non-numeric values
            if value == '--' or pd.isna(value):
                monthly_values.append(None)
            else:
                try:
                    monthly_values.append(float(value))
                except (ValueError, TypeError):
                    monthly_values.append(None)
        
        # Calculate average of available values
        valid_values = [v for v in monthly_values if v is not None]
        avg_aqi = round(sum(valid_values) / len(valid_values), 1) if valid_values else None
        
        # Find peak and cleanest months
        if valid_values:
            peak_value = max(valid_values)
            peak_month_index = monthly_values.index(peak_value)
            peak_month = full_month_names[peak_month_index]
            
            cleanest_value = min(valid_values)
            cleanest_month_index = monthly_values.index(cleanest_value)
            cleanest_month = full_month_names[cleanest_month_index]
        else:
            peak_value = None
            peak_month = "N/A"
            cleanest_value = None
            cleanest_month = "N/A"
        
        # Calculate seasonal averages
        # Winter: Dec, Jan, Feb
        winter_months = [monthly_values[0], monthly_values[1], monthly_values[11]] # Jan, Feb, Dec
        winter_avg = round(sum([v for v in winter_months if v is not None]) / len([v for v in winter_months if v is not None]), 1) if any(v is not None for v in winter_months) else "N/A"
        
        # Summer: Mar, Apr, May
        summer_months = [monthly_values[2], monthly_values[3], monthly_values[4]] # Mar, Apr, May
        summer_avg = round(sum([v for v in summer_months if v is not None]) / len([v for v in summer_months if v is not None]), 1) if any(v is not None for v in summer_months) else "N/A"
        
        # Monsoon: Jun, Jul, Aug, Sep
        monsoon_months = [monthly_values[5], monthly_values[6], monthly_values[7], monthly_values[8]] # Jun, Jul, Aug, Sep
        monsoon_avg = round(sum([v for v in monsoon_months if v is not None]) / len([v for v in monsoon_months if v is not None]), 1) if any(v is not None for v in monsoon_months) else "N/A"
        
        # Post-Monsoon: Oct, Nov
        post_monsoon_months = [monthly_values[9], monthly_values[10]] # Oct, Nov
        post_monsoon_avg = round(sum([v for v in post_monsoon_months if v is not None]) / len([v for v in post_monsoon_months if v is not None]), 1) if any(v is not None for v in post_monsoon_months) else "N/A"
        
        return {
            'country': country_name,
            'rank': row.get('Rank', 'N/A'),
            'display': country_name,
            'monthly_values': monthly_values,
            'monthly_data': monthly_values, # Duplicate for the chart
            'avg_aqi': avg_aqi,
            'peak_month': peak_month,
            'peak_value': peak_value,
            'cleanest_month': cleanest_month,
            'cleanest_value': cleanest_value,
            'seasonal_data': {
                'winter': winter_avg,
                'summer': summer_avg,
                'monsoon': monsoon_avg,
                'post_monsoon': post_monsoon_avg
            }
        }
    except Exception as e:
        logger.error(f"Error processing country data: {e}")
        return None

def calculate_weather_impact(weather):
    """Calculate the weather impact on future air quality"""
    impact = 0
    
    # Higher humidity can trap pollutants
    if 'h' in weather and isinstance(weather['h'], dict) and 'v' in weather['h']:
        humidity = float(weather['h']['v'])
        if humidity > 70:
            impact += 0.05
        elif humidity < 40:
            impact -= 0.03
    
    # Wind speed affects dispersion of pollutants
    if 'w' in weather and isinstance(weather['w'], dict) and 'v' in weather['w']:
        wind = float(weather['w']['v'])
        if wind > 20:  # Strong wind improves air quality
            impact -= 0.1
        elif wind < 5:  # Light wind worsens air quality
            impact += 0.05
    
    # Precipitation improves air quality
    if 'p' in weather and isinstance(weather['p'], dict) and 'v' in weather['p']:
        precipitation = float(weather['p']['v'])
        if precipitation > 0:
            impact -= 0.07
    
    # Temperature can affect chemical reactions in atmosphere
    if 't' in weather and isinstance(weather['t'], dict) and 'v' in weather['t']:
        temp = float(weather['t']['v'])
        if temp > 30:  # Hot weather can increase ozone
            impact += 0.05
        elif temp < 0:  # Cold weather can increase PM2.5 due to heating
            impact += 0.03
    
    return impact

@app.route('/api/weather-correlation')
def weather_correlation():
    """
    API endpoint to get correlation data between weather parameters and air quality
    """
    try:
        # Default correlation data that would normally come from analysis of historical data
        correlation_data = {
            "parameters": ["Temperature", "Humidity", "Wind Speed", "Pressure", "Precipitation"],
            "pm25_correlation": [0.35, 0.45, -0.62, 0.1, -0.48],
            "o3_correlation": [0.72, -0.3, -0.35, 0.05, -0.25],
            "no2_correlation": [0.15, 0.2, -0.55, 0.12, -0.4],
            "so2_correlation": [0.08, 0.25, -0.45, 0.15, -0.38],
            "co_correlation": [0.3, 0.15, -0.6, 0.05, -0.42],
            "explanations": [
                "Higher temperatures can accelerate chemical reactions that form pollutants, especially ozone.",
                "Higher humidity can increase the concentration of fine particles as water molecules attach to them.",
                "Stronger winds typically disperse pollutants and improve air quality across all categories.",
                "Atmospheric pressure has minimal direct effect on air quality but can influence air mass movement.",
                "Precipitation helps remove pollutants from the air through wet deposition, improving air quality."
            ]
        }
        
        # Seasonal patterns data
        seasonal_patterns = {
            "winter": {
                "description": "Winter often sees higher levels of PM2.5 and NO2 due to increased heating, less vertical mixing in the atmosphere, and temperature inversions that trap pollution.",
                "pollutants": {
                    "pm25": "Typically elevated due to home heating, especially in areas using solid fuels.",
                    "o3": "Usually lower due to reduced sunlight and lower temperatures.",
                    "no2": "Often higher in urban areas due to vehicle emissions and reduced dispersion.",
                    "so2": "Can be elevated in areas with coal-based heating systems.",
                    "co": "Typically higher due to heating and vehicle cold starts."
                }
            },
            "spring": {
                "description": "Spring can bring variable conditions with occasional pollen-related particulate matter increases and rising ozone as temperatures increase.",
                "pollutants": {
                    "pm25": "Can be affected by agricultural activities and dust storms in some regions.",
                    "o3": "Begins to increase with longer daylight hours and stronger solar radiation.",
                    "no2": "Moderate levels that may decrease with improved atmospheric mixing.",
                    "so2": "Generally moderate as heating needs decrease.",
                    "co": "Decreases as heating requirements diminish and vehicle efficiency improves in warmer weather."
                }
            },
            "summer": {
                "description": "Summer typically has the highest ozone levels due to intense sunlight and heat, while other pollutants may decrease with better atmospheric mixing.",
                "pollutants": {
                    "pm25": "Can be elevated due to wildfires in some regions, otherwise moderate to low.",
                    "o3": "Highest levels of the year due to strong sunlight and high temperatures.",
                    "no2": "Generally lower due to improved vertical mixing in the atmosphere.",
                    "so2": "Typically at annual lows as heating is minimal.",
                    "co": "Usually lower due to efficient vehicle operation and minimal heating."
                }
            },
            "fall": {
                "description": "Fall brings transitional conditions with decreasing ozone and gradually increasing particulate matter as heating requirements return.",
                "pollutants": {
                    "pm25": "May increase as heating systems are reactivated and open burning occurs in some areas.",
                    "o3": "Decreases from summer peaks as sunlight intensity and duration diminish.",
                    "no2": "Begins to increase as atmospheric conditions become less favorable for dispersion.",
                    "so2": "Slight increases as heating systems become more active.",
                    "co": "Gradual increase with colder temperatures and increased heating needs."
                }
            }
        }
        
        return jsonify({
            "correlations": correlation_data,
            "seasonal_patterns": seasonal_patterns
        })
    except Exception as e:
        logger.error(f"Error getting weather correlation data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/seasonal-forecast')
def seasonal_forecast():
    """
    API endpoint to get seasonal air quality predictions
    """
    try:
        # Generate seasonal forecast data
        # In a real application, this would use historical seasonal patterns and climate models
        seasons = ["winter", "spring", "summer", "fall"]
        location_types = ["urban", "suburban", "rural"]
        
        # Current season based on northern hemisphere calendar
        current_month = datetime.now().month
        current_season_idx = (current_month % 12) // 3
        
        # Generate forecasts for upcoming seasons (starting with the current one)
        forecasts = []
        
        for i in range(4):  # Four seasons
            season_idx = (current_season_idx + i) % 4
            season = seasons[season_idx]
            
            # Location-specific forecasts
            locations = {}
            
            for location_type in location_types:
                # Base AQI varies by location type and season
                if location_type == "urban":
                    base_aqi = 85
                elif location_type == "suburban":
                    base_aqi = 60
                else:  # rural
                    base_aqi = 40
                
                # Seasonal adjustment
                if season == "winter":
                    # Winter typically has higher pollution in urban areas due to heating
                    # and temperature inversions trapping pollution
                    if location_type == "urban":
                        seasonal_factor = 1.3
                    elif location_type == "suburban":
                        seasonal_factor = 1.2
                    else:  # rural
                        seasonal_factor = 1.1
                elif season == "summer":
                    # Summer has higher ozone but sometimes lower other pollutants
                    # Urban areas suffer more from ozone due to traffic and heat islands
                    if location_type == "urban":
                        seasonal_factor = 1.1
                    elif location_type == "suburban":
                        seasonal_factor = 0.9
                    else:  # rural
                        seasonal_factor = 0.8
                elif season == "spring":
                    # Spring can have mixed conditions but generally improving air quality
                    seasonal_factor = 0.9
                else:  # fall
                    # Fall has transitional conditions, gradually worsening as heating season begins
                    seasonal_factor = 1.0
                
                # Calculate predicted AQI
                predicted_aqi = int(base_aqi * seasonal_factor)
                
                # Determine category based on AQI
                category = health_utils.get_aqi_category(predicted_aqi)
                
                # Get color code
                color = api_utils.get_aqi_category_and_color(predicted_aqi)[1]
                
                # Predominant pollutant varies by season and location
                if season == "winter":
                    predominant = "pm25" if location_type in ["urban", "suburban"] else "pm10"
                elif season == "summer":
                    predominant = "o3"
                elif season == "spring":
                    predominant = "pm10" if location_type == "rural" else "o3"
                else:  # fall
                    predominant = "pm25" if location_type == "urban" else "o3"
                
                # Generate pollutant levels based on the AQI and predominant pollutant
                pollutant_levels = {
                    "pm25": round(predicted_aqi * 0.6 if predominant == "pm25" else predicted_aqi * 0.3, 1),
                    "o3": round(predicted_aqi * 0.5 if predominant == "o3" else predicted_aqi * 0.25, 1),
                    "no2": round(predicted_aqi * 0.4 if predominant == "no2" else predicted_aqi * 0.2, 1),
                    "so2": round(predicted_aqi * 0.3 if predominant == "so2" else predicted_aqi * 0.15, 1),
                    "co": round(predicted_aqi * 0.02 if predominant == "co" else predicted_aqi * 0.01, 1)
                }
                
                locations[location_type] = {
                    "aqi": predicted_aqi,
                    "category": category,
                    "color": color,
                    "predominant_pollutant": predominant,
                    "pollutant_levels": pollutant_levels
                }
            
            forecasts.append({
                "season": season,
                "locations": locations
            })
        
        return jsonify({
            "forecasts": forecasts
        })
    except Exception as e:
        logger.error(f"Error generating seasonal forecast: {e}")
        return jsonify({"error": str(e)}), 500

# Health recommendations API endpoint removed as requested

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5500, debug=True)
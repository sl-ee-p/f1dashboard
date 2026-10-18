import os
import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import fastf1
import pandas as pd
import json

# Setup FastF1 cache
cache_dir = 'cache'
if not os.path.exists(cache_dir):
    os.makedirs(cache_dir)
fastf1.Cache.enable_cache(cache_dir)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'f1_secret_key'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('load_session')
def handle_load_session(data):
    year = int(data.get('year'))
    round_num = int(data.get('round'))
    session_type = data.get('session')
    
    emit('status', {'msg': f'Loading {year} Round {round_num} - {session_type}...'})
    
    try:
        session = fastf1.get_session(year, round_num, session_type)
        session.load()
        
        # 1. Driver Positions & Info
        drivers = session.drivers
        driver_results = []
        for drv in drivers:
            info = session.get_driver(drv)
            driver_results.append({
                'number': drv,
                'name': info['FullName'],
                'team': info['TeamName'],
                'color': f"#{info['TeamColor']}" if info['TeamColor'] else '#FFFFFF',
                'abbreviation': info['Abbreviation']
            })
            
        # 2. Lap Times (Top 10 or all)
        laps = session.laps.pick_quicklaps()
        fastest_lap = laps.pick_fastest()
        
        # 3. Tyre Strategy
        # Group by driver and tyre compound
        stints = session.laps[['Driver', 'Stint', 'Compound', 'LapNumber']].groupby(['Driver', 'Stint', 'Compound']).count().reset_index()
        tyre_data = stints.to_dict(orient='records')

        # 4. Basic Telemetry (Fastest Lap of fastest driver as example)
        telemetry = fastest_lap.get_telemetry()
        # Sampling telemetry to avoid huge payload
        telemetry_sampled = telemetry.iloc[::10, :] # Every 10th point
        telemetry_data = {
            'speed': telemetry_sampled['Speed'].tolist(),
            'distance': telemetry_sampled['Distance'].tolist(),
            'throttle': telemetry_sampled['Throttle'].tolist(),
            'brake': telemetry_sampled['Brake'].tolist(),
            'driver': fastest_lap['Driver']
        }

        emit('session_data', {
            'drivers': driver_results,
            'tyre_strategy': tyre_data,
            'telemetry': telemetry_data,
            'session_name': f"{session.event['EventName']} {year}"
        })
        emit('status', {'msg': 'Session loaded successfully!'})
        
    except Exception as e:
        emit('status', {'msg': f'Error loading session: {str(e)}'})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

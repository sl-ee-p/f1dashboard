import os
import json
import pandas as pd
import fastf1
import threading
from flask import Flask, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'f1dashboard_secret'
socketio = SocketIO(app, cors_allowed_origins="*")

os.makedirs('cache', exist_ok=True)
fastf1.Cache.enable_cache('cache')

live_streaming = False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/live')
def live():
    return render_template('live.html')

def live_stream_worker():
    global live_streaming
    try:
        while live_streaming:
            socketio.emit('live_data', {'type': 'status', 'content': 'Listening for F1 live session...'})
            socketio.sleep(2)
    except Exception as e:
        socketio.emit('live_error', {'msg': str(e)})

@socketio.on('start_live')
def handle_start_live():
    global live_streaming
    if not live_streaming:
        live_streaming = True
        emit('status', {'msg': 'Connecting to F1 Live Timing...'})
        socketio.start_background_task(live_stream_worker)

@socketio.on('stop_live')
def handle_stop_live():
    global live_streaming
    live_streaming = False
    emit('status', {'msg': 'Live feed stopped.'})

def format_laptime(lt):
    if pd.isnull(lt):
        return 'N/A'
    total = lt.total_seconds()
    m = int(total // 60)
    s = total % 60
    return f"{m}:{s:06.3f}"

@socketio.on('load_session')
def handle_load_session(data):
    try:
        year = int(data.get('year', 2023))
        gp = data.get('gp', 'Monaco')
        session_type = data.get('session', 'R')

        emit('status', {'msg': f'Loading {year} {gp} {session_type}...'})

        session = fastf1.get_session(year, gp, session_type)
        session.load(telemetry=True, laps=True, weather=False)

        # Results
        drivers_data = []
        if not session.results.empty:
            for _, d in session.results.iterrows():
                drivers_data.append({
                    'number': str(d.get('DriverNumber', '??')),
                    'abbreviation': str(d.get('Abbreviation', '???')),
                    'team': str(d.get('TeamName', 'Unknown')),
                    'position': int(d['Position']) if pd.notnull(d.get('Position')) else 0,
                    'grid': int(d['GridPosition']) if pd.notnull(d.get('GridPosition')) else 0,
                    'status': str(d.get('Status', 'N/A')),
                    'points': float(d['Points']) if pd.notnull(d.get('Points')) else 0,
                })

        # Tyre strategy — all stints per driver, not just fastest lap
        laps = session.laps
        tyre_data = []
        if not laps.empty:
            for drv_id in session.drivers:
                driver_laps = laps.pick_driver(drv_id)
                if driver_laps.empty:
                    continue
                try:
                    fastest = driver_laps.pick_fastest()
                    drv_info = session.get_driver(drv_id)

                    # Count stints
                    stints = driver_laps['Stint'].nunique() if 'Stint' in driver_laps.columns else 1

                    tyre_data.append({
                        'driver': drv_info['Abbreviation'],
                        'team': str(drv_info.get('TeamName', '')),
                        'compound': str(fastest['Compound']),
                        'lap_time': format_laptime(fastest['LapTime']),
                        'tyre_life': int(fastest['TyreLife']) if pd.notnull(fastest['TyreLife']) else 0,
                        'stints': stints,
                        'total_laps': len(driver_laps),
                    })
                except Exception as e:
                    print(f"Skipping driver {drv_id}: {e}")

        # Telemetry for overall fastest lap
        tel_data = None
        if not laps.empty:
            try:
                fastest_lap = laps.pick_fastest()
                if fastest_lap is not None and not fastest_lap.empty:
                    tel = fastest_lap.get_telemetry()
                    if not tel.empty:
                        tel = tel.iloc[::8]
                        gear = tel['nGear'].tolist() if 'nGear' in tel.columns else []
                        tel_data = {
                            'distance': [round(x, 1) for x in tel['Distance'].tolist()],
                            'speed': [round(x, 1) for x in tel['Speed'].tolist()],
                            'throttle': [round(x, 1) for x in tel['Throttle'].tolist()],
                            'brake': tel['Brake'].tolist(),
                            'gear': gear,
                            'driver': str(fastest_lap['Driver']),
                        }
            except Exception as e:
                print(f"Telemetry error: {e}")

        # Lap time progression for top 5
        lap_progression = []
        if not laps.empty:
            try:
                top5 = [d['abbreviation'] for d in sorted(drivers_data, key=lambda x: x['position']) if d['position'] > 0][:5]
                for abbr in top5:
                    drv_laps = laps.pick_driver(abbr)
                    valid = drv_laps[drv_laps['LapTime'].notnull()]
                    if not valid.empty:
                        lap_progression.append({
                            'driver': abbr,
                            'laps': valid['LapNumber'].tolist(),
                            'times': [round(lt.total_seconds(), 3) for lt in valid['LapTime']],
                        })
            except Exception as e:
                print(f"Lap progression error: {e}")

        emit('session_data', {
            'drivers': drivers_data,
            'tyres': tyre_data,
            'telemetry': tel_data,
            'lap_progression': lap_progression,
            'session_name': f"{year} {session.event['EventName']} — {session.name}",
        })
        emit('status', {'msg': 'Session loaded successfully.'})

    except Exception as e:
        import traceback
        traceback.print_exc()
        emit('error', {'msg': str(e)})

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)

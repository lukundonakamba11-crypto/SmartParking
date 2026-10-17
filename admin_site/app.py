import os
import sys
import threading
import time
import subprocess
import queue
import base64
import requests
import numpy as np
import cv2
import json
import webbrowser
from datetime import datetime, timedelta
import glob
from functools import wraps

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit
from database import DatabaseManager


# -----------------------------
# Configuration
# -----------------------------
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PYTHON_CLIENT_DIR = os.path.join(PROJECT_ROOT, 'python-client')

NODE_SERVER_URL = os.environ.get('NODE_SERVER_URL', 'http://localhost:3000')

# Initialize database
db = DatabaseManager()


# -----------------------------
# Flask + SocketIO
# -----------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = 'change_me_in_prod'
# Force threading async mode to avoid Eventlet/SSL issues on Windows/Python 3.13
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# Cache latest detection snapshot for HTTP polling fallback
LAST_DETECTION = {
    'free_slots': None,
    'total_slots': None,
    'slots': None,
    'ts': None,
}


# -----------------------------
# Subprocess Management
# -----------------------------
class ManagedProcess:
    def __init__(self, name: str, script_path: str, args=None, env=None):
        self.name = name
        self.script_path = script_path
        self.args = args or []
        self.env = env or {}
        self.proc = None
        self.stdout_thread = None
        self.stderr_thread = None
        self._stop_evt = threading.Event()

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not os.path.isfile(self.script_path):
            print(f"Cannot start '{self.name}': script not found at {self.script_path}")
            return

        python_exe = sys.executable
        cmd = [python_exe, self.script_path] + self.args
        self._stop_evt.clear()
        
        # For GUI applications, we need to handle stdout/stderr differently
        # to prevent interference with OpenCV windows
        if self.name in ['live', 'calibration', 'viewer']:
            # GUI applications - don't capture stdout/stderr to avoid blocking
            # On Windows, suppress console window creation
            creation_flags = 0
            if os.name == 'nt':  # Windows
                creation_flags = subprocess.CREATE_NO_WINDOW

            self.proc = subprocess.Popen(
                cmd,
                cwd=PYTHON_CLIENT_DIR,
                env={**os.environ, **self.env},
                creationflags=creation_flags
            )
            # Start a separate thread to monitor the process
            self.monitor_thread = threading.Thread(target=self._monitor_gui_process, daemon=True)
            self.monitor_thread.start()
        else:
            # Non-GUI applications - capture stdout/stderr as before
            creation_flags = 0
            if os.name == 'nt':
                creation_flags = subprocess.CREATE_NO_WINDOW

            self.proc = subprocess.Popen(
                cmd,
                cwd=PYTHON_CLIENT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                universal_newlines=True,
                env={**os.environ, **self.env},
                creationflags=creation_flags
            )
            self.stdout_thread = threading.Thread(target=self._reader, args=(self.proc.stdout, False), daemon=True)
            self.stderr_thread = threading.Thread(target=self._reader, args=(self.proc.stderr, True), daemon=True)
            self.stdout_thread.start()
            self.stderr_thread.start()

    def _reader(self, stream, is_err: bool):
        try:
            for line in iter(stream.readline, ''):
                if self._stop_evt.is_set():
                    break
                # Forward raw logs
                socketio.emit('script_log', {
                    'script': self.name,
                    'level': 'stderr' if is_err else 'stdout',
                    'line': line.rstrip('\n')
                })
                # Try to detect JSON occupancy lines and re-emit structured
                try:
                    payload = line.strip()
                    if payload.startswith('OCCUPANCY:'):
                        payload = payload.split('OCCUPANCY:', 1)[1].strip()
                    if payload.startswith('{') and not is_err:
                        import json as _json
                        data = _json.loads(payload)
                        # Broadcast to dashboards
                        socketio.emit('detection_update', data)
                        # Update cache for polling fallbacks
                        try:
                            from datetime import datetime as _dt
                            free = data.get('free_slots')
                            total = data.get('total_slots')
                            slots = data.get('slots') if isinstance(data.get('slots'), list) else None
                            if (free is None or total is None) and isinstance(slots, list):
                                total = len(slots)
                                free = len([s for s in slots if s.get('is_free') is True or s.get('status') == 'free' or s.get('occupied') is False])
                            LAST_DETECTION.update({
                                'free_slots': free,
                                'total_slots': total,
                                'slots': slots,
                                'ts': _dt.now().isoformat(),
                            })
                        except Exception:
                            pass
                except Exception:
                    pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _monitor_gui_process(self):
        """Monitor GUI processes without capturing stdout/stderr"""
        try:
            while self.proc and self.proc.poll() is None:
                if self._stop_evt.is_set():
                    break
                time.sleep(0.1)
            
            # Process has ended
            if self.proc:
                return_code = self.proc.poll()
                if return_code is not None:
                    print(f"GUI process {self.name} ended with code {return_code}")
        except Exception as e:
            print(f"Error monitoring GUI process {self.name}: {e}")

    def stop(self):
        self._stop_evt.set()
        if self.proc and self.proc.poll() is None:
            try:
                # Try graceful termination first
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    # Force kill if graceful termination fails
                    print(f"Force killing process {self.name}")
                    self.proc.kill()
                    try:
                        self.proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        print(f"Failed to kill process {self.name}")
            except Exception as e:
                print(f"Error stopping process {self.name}: {e}")
        self.proc = None

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


# Environment for Python scripts - allow GUI windows to open
PYTHON_ENV = {
    'PYTHONIOENCODING': 'utf-8',
    'PYTHONUNBUFFERED': '1',  # Ensure output is not buffered
    'DISPLAY': ':0.0' if os.name != 'nt' else None,  # For Linux GUI
}
# Remove None values
PYTHON_ENV = {k: v for k, v in PYTHON_ENV.items() if v is not None}

processes = {
    'live': ManagedProcess('live', os.path.join(PYTHON_CLIENT_DIR, 'live_stream_viewer.py'), env=PYTHON_ENV),
    'calibration': ManagedProcess('calibration', os.path.join(PYTHON_CLIENT_DIR, 'calibrate_slots.py'), env=PYTHON_ENV),
    'detection': ManagedProcess('detection', os.path.join(PYTHON_CLIENT_DIR, 'detection.py'), env=PYTHON_ENV),
    'viewer': ManagedProcess('viewer', os.path.join(PYTHON_CLIENT_DIR, 'live_stream_viewer.py'), env=PYTHON_ENV),
}


# -----------------------------
# Frame Fetcher (from Node /frame)
# -----------------------------
def frame_to_base64(frame) -> str:
    ok, buf = cv2.imencode('.jpg', frame)
    if not ok:
        return ''
    return 'data:image/jpeg;base64,' + base64.b64encode(buf).decode('utf-8')


def get_frame_from_node() -> np.ndarray:
    try:
        r = requests.get(f"{NODE_SERVER_URL}/frame", timeout=5)
        if r.status_code != 200:
            return None
        nparr = np.frombuffer(r.content, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return frame
    except Exception:
        return None


class LiveFrameStreamer:
    def __init__(self, interval_sec: float = 0.2):
        self.interval = interval_sec
        self._stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        if not self.thread.is_alive():
            self._stop_evt.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        self._stop_evt.set()

    def _run(self):
        while not self._stop_evt.is_set():
            frame = get_frame_from_node()
            if frame is not None:
                socketio.emit('live_frame', {'frame': frame_to_base64(frame)})
            time.sleep(self.interval)


live_streamer = LiveFrameStreamer()


# -----------------------------
# Video Recording System
# -----------------------------
class VideoRecorder:
    def __init__(self, output_dir: str = "saved_videos"):
        self.output_dir = output_dir
        self.is_recording = False
        self.current_writer = None
        self.current_filename = None
        self.start_time = None
        self.recording_thread = None
        self._stop_event = threading.Event()
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
    
    def start_recording(self):
        """Start recording video from ESP32-CAM stream"""
        if self.is_recording:
            return
        
        self.is_recording = True
        self._stop_event.clear()
        self.start_time = datetime.now()
        
        # Generate filename with current timestamp
        timestamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        self.current_filename = f"parking_recording_{timestamp}.mp4"
        file_path = os.path.join(self.output_dir, self.current_filename)
        
        print(f"Starting recording at: {self.start_time}")
        print(f"Generated filename: {self.current_filename}")
        
        # Start recording thread
        self.recording_thread = threading.Thread(target=self._record_loop, daemon=True)
        self.recording_thread.start()
        
        print(f"Started recording: {self.current_filename}")
    
    def stop_recording(self):
        """Stop recording and save video"""
        if not self.is_recording:
            return
        
        self.is_recording = False
        self._stop_event.set()
        
        if self.recording_thread and self.recording_thread.is_alive():
            self.recording_thread.join(timeout=5)
        
        if self.current_writer:
            self.current_writer.release()
            self.current_writer = None
        
        # Calculate duration and file size
        if self.start_time and self.current_filename:
            end_time = datetime.now()
            duration = (end_time - self.start_time).total_seconds()
            file_path = os.path.join(self.output_dir, self.current_filename)
            
            print(f"Stopping recording at: {end_time}")
            print(f"Duration: {duration} seconds")
            print(f"File path: {file_path}")
            
            if os.path.exists(file_path):
                file_size = os.path.getsize(file_path)
                
                # Save to database
                video_data = {
                    'filename': self.current_filename,
                    'file_path': file_path,
                    'start_time': self.start_time.isoformat(),
                    'end_time': end_time.isoformat(),
                    'duration_seconds': int(duration),
                    'file_size_bytes': file_size
                }
                
                print(f"Saving video data: {video_data}")
                
                try:
                    video_id = db.add_saved_video(video_data)
                    print(f"Video saved to database with ID: {video_id}")
                except Exception as e:
                    print(f"Error saving video to database: {e}")
            else:
                print(f"Warning: Video file not found at {file_path}")
        
        print(f"Stopped recording: {self.current_filename}")
        self.current_filename = None
        self.start_time = None
    
    def _record_loop(self):
        """Main recording loop"""
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        fps = 10  # Adjust based on ESP32-CAM capabilities
        frame_size = (640, 480)  # Adjust based on ESP32-CAM resolution
        frame_count = 0
        max_frames = 864000  # 24 hours at 10 FPS (864000 frames)
        
        while not self._stop_event.is_set() and self.is_recording and frame_count < max_frames:
            try:
                frame = get_frame_from_node()
                if frame is not None:
                    if self.current_writer is None:
                        file_path = os.path.join(self.output_dir, self.current_filename)
                        self.current_writer = cv2.VideoWriter(file_path, fourcc, fps, frame_size)
                        if not self.current_writer.isOpened():
                            print(f"Error: Could not open video writer for {file_path}")
                            break
                    
                    # Resize frame if necessary
                    if frame.shape[:2] != frame_size:
                        frame = cv2.resize(frame, frame_size)
                    
                    self.current_writer.write(frame)
                    frame_count += 1
                else:
                    # If no frame available, wait a bit longer
                    time.sleep(0.5)
                
                time.sleep(0.1)  # 10 FPS
            except Exception as e:
                print(f"Error in recording loop: {e}")
                time.sleep(1)
        
        if self.current_writer:
            self.current_writer.release()
            self.current_writer = None
    
    def is_recording_active(self):
        """Check if recording is currently active"""
        return self.is_recording and not self._stop_event.is_set()
    
    def get_recording_info(self):
        """Get current recording information"""
        if not self.is_recording:
            return None
        
        return {
            'filename': self.current_filename,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'duration': (datetime.now() - self.start_time).total_seconds() if self.start_time else 0
        }


# Initialize video recorder
video_recorder = VideoRecorder()


# -----------------------------
# Calibration state managed in Flask
# -----------------------------
CALIBRATION_SLOTS = []  # list of {id, points: [[x,y],...], name}
CURRENT_POLY = []
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'python-client', 'config')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'prototype_slots.json')

def persist_slots():
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        import json as _json
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            _json.dump(CALIBRATION_SLOTS, f, indent=2)
        socketio.emit('calibration_saved', {'ok': True, 'path': CONFIG_PATH})
    except Exception as e:
        socketio.emit('calibration_saved', {'ok': False, 'error': str(e)})


# -----------------------------
# Authentication Routes
# -----------------------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if db.authenticate_admin(username, password):
            session['admin_logged_in'] = True
            session['admin_username'] = username
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('login'))

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# -----------------------------
# Flask routes
# -----------------------------
@app.route('/')
def index():
    if session.get('admin_logged_in'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/admin')
def admin():
    if session.get('admin_logged_in'):
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# Controls are now integrated into the dashboard

@app.route('/client')
def client():
    """Client reservation portal (no authentication required)"""
    return render_template('client.html')


# -----------------------------
# API Routes
# -----------------------------
@app.route('/api/occupancy')
@login_required
def api_occupancy():
    """Get parking occupancy statistics"""
    # Prefer live detection snapshot if available
    try:
        if LAST_DETECTION.get('free_slots') is not None and LAST_DETECTION.get('total_slots') is not None:
            free = LAST_DETECTION['free_slots']
            total = LAST_DETECTION['total_slots']
            occupied = (total - free) if (total is not None and free is not None) else None
            rate = (100.0 * occupied / total) if total else 0.0
            return jsonify({
                'free_slots': free,
                'total_slots': total,
                'occupied_slots': occupied,
                'occupancy_rate': rate,
                'source': 'detection'
            })
    except Exception:
        pass
    # Fallback to DB stats
    stats = db.get_occupancy_stats()
    stats['source'] = 'db'
    return jsonify(stats)

@app.route('/api/next-available-time')
@login_required
def api_next_available_time():
    """Get next available time for parking slots"""
    try:
        next_time = db.get_next_available_time()
        return jsonify({
            'next_available_time': next_time.isoformat() if next_time else None
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/calibration/slots')
@login_required
def api_calibration_slots():
    """Return calibration slots from saved file"""
    try:
        if not os.path.isfile(CONFIG_PATH):
            return jsonify({ 'slots': [], 'count': 0 })
        import json as _json
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            slots = _json.load(f)
        count = len(slots) if isinstance(slots, list) else 0
        return jsonify({ 'slots': slots if isinstance(slots, list) else [], 'count': count })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/slots')
@login_required
def api_slots():
    """Get all parking slots"""
    slots = db.get_all_parking_slots()
    return jsonify(slots)

@app.route('/api/requests')
@login_required
def api_requests():
    """Get all reservation requests"""
    requests = db.get_all_requests()
    return jsonify(requests)

@app.route('/api/requests/pending')
@login_required
def api_pending_requests():
    """Get pending reservation requests"""
    requests = db.get_pending_requests()
    return jsonify(requests)

@app.route('/api/reservations')
@login_required
def api_reservations():
    """Get all reservations"""
    reservations = db.get_all_reservations()
    return jsonify(reservations)

@app.route('/api/reservations/active')
@login_required
def api_active_reservations():
    """Get active reservations"""
    reservations = db.get_active_reservations()
    return jsonify(reservations)

@app.route('/api/requests/<int:request_id>/accept', methods=['POST'])
@login_required
def api_accept_request(request_id):
    """Accept a reservation request"""
    data = request.get_json() or {}
    slot_id = data.get('slot_id')
    mark_paid = data.get('mark_paid', False)
    
    try:
        # Optionally mark request as paid before accepting
        if mark_paid:
            try:
                db.update_request_payment_status(request_id, 'paid')
            except Exception:
                pass

        # Get client data from the request
        request_data = db.get_request_by_id(request_id)
        client_data = None
        if request_data and request_data.get('client_data'):
            try:
                client_data = json.loads(request_data['client_data'])
            except (json.JSONDecodeError, TypeError):
                pass

        reservation_id = db.accept_request(request_id, slot_id, client_data)
        if reservation_id:
            # Enrich payload with reservation details for client notification
            reservation = db.get_reservation_by_id(reservation_id)
            try:
                socketio.emit('request_accepted', {
                    'request_id': request_id,
                    'reservation_id': reservation_id,
                    'client_id': reservation.get('client_id') if reservation else None,
                    'number_plate': reservation.get('number_plate') if reservation else None,
                    'start_time': reservation.get('start_time') if reservation else None,
                    'end_time': reservation.get('end_time') if reservation else None,
                    'message': 'Request accepted successfully'
                })
            except Exception:
                pass
            return jsonify({'success': True, 'reservation_id': reservation_id})
        else:
            return jsonify({'success': False, 'error': 'Request not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/requests/<int:request_id>/reject', methods=['POST'])
@login_required
def api_reject_request(request_id):
    """Reject a reservation request"""
    try:
        # Before rejection, try to fetch the request for identifier context
        req = None
        try:
            req = db.get_request_by_id(request_id)
        except Exception:
            pass
        db.reject_request(request_id)
        try:
            socketio.emit('request_rejected', {
                'request_id': request_id,
                'number_plate': (req or {}).get('number_plate') if isinstance(req, dict) else None,
                'message': 'Request rejected'
            })
        except Exception:
            pass
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/reservations/<int:reservation_id>/cancel', methods=['POST'])
@login_required
def api_cancel_reservation(reservation_id):
    """Cancel a reservation"""
    try:
        db.cancel_reservation(reservation_id)
        # Emit cancellation with identifiers
        try:
            reservation = db.get_reservation_by_id(reservation_id)
            socketio.emit('reservation_cancelled', {
                'reservation_id': reservation_id,
                'client_id': reservation.get('client_id') if reservation else None,
                'number_plate': reservation.get('number_plate') if reservation else None,
                'start_time': reservation.get('start_time') if reservation else None,
                'end_time': reservation.get('end_time') if reservation else None,
                'message': 'Reservation cancelled'
            })
        except Exception:
            pass
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# -----------------------------
# Client API Routes (No authentication required)
# -----------------------------
@app.route('/api/client/availability', methods=['GET'])
def api_client_availability():
    """Get parking availability for clients"""
    try:
        stats = db.get_occupancy_stats()
        next_available_time = db.get_next_available_time()
        
        response_data = {
            'available': stats['free_slots'] > 0,
            'free_slots': stats['free_slots'],
            'total_slots': stats['total_slots'],
            'reserved_slots': stats['reserved_slots'],
            'message': 'Parking available' if stats['free_slots'] > 0 else 'Parking lot full'
        }
        
        # Add next available time if parking is full
        if stats['free_slots'] == 0 and next_available_time:
            response_data['next_available_time'] = next_available_time
            response_data['message'] = f'Parking full - Next slot available at {next_available_time}'
        
        return jsonify(response_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/reserve', methods=['POST'])
def api_client_reserve():
    """Create a reservation request from client"""
    try:
        data = request.get_json()
        print(f"Client reservation request received: {data}")
        
        if not data:
            return jsonify({'error': 'No data received'}), 400
        
        # Validate required fields
        required_fields = ['name', 'number_plate', 'duration']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Check availability
        stats = db.get_occupancy_stats()
        print(f"Current parking stats: {stats}")
        
        if stats['free_slots'] <= 0:
            return jsonify({'error': 'Parking lot is full'}), 400
        
        # Create reservation request
        request_data = {
            'name': data['name'],
            'number_plate': data['number_plate'],
            'duration': int(data['duration']),  # Ensure duration is integer
            'start_time': data.get('start_time'),
            'payment_status': data.get('payment_status', 'pending')
        }
        
        print(f"Creating reservation request: {request_data}")
        request_id = db.add_reservation_request(request_data)
        print(f"Reservation request created with ID: {request_id}")
        
        return jsonify({
            'success': True,
            'request_id': request_id,
            'message': 'Reservation request submitted successfully'
        })
        
    except Exception as e:
        print(f"Error in client reservation: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/request/<int:request_id>/status', methods=['GET'])
def api_client_request_status(request_id):
    """Get status of a reservation request"""
    try:
        requests = db.get_all_requests()
        request_data = next((req for req in requests if req['request_id'] == request_id), None)
        
        if not request_data:
            return jsonify({'error': 'Request not found'}), 404
        
        return jsonify({
            'request_id': request_data['request_id'],
            'status': request_data['status'],
            'name': request_data['name'],
            'number_plate': request_data['number_plate'],
            'duration': request_data['duration'],
            'payment_status': request_data['payment_status'],
            'created_at': request_data['created_at']
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -----------------------------
# Video Management API Routes
# -----------------------------
@app.route('/api/videos')
@login_required
def api_get_videos():
    """Get all saved videos with optional date and time filtering"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        search_time = request.args.get('search_time')
        
        print(f"API: Getting videos with start_date={start_date}, end_date={end_date}, search_time={search_time}")
        
        videos = db.get_saved_videos(start_date, end_date, search_time)
        
        print(f"API: Found {len(videos)} videos")
        
        # Add file existence check and format file sizes
        for video in videos:
            video['file_exists'] = os.path.exists(video['file_path'])
            video['file_size_mb'] = round(video['file_size_bytes'] / (1024 * 1024), 2)
            video['duration_minutes'] = round(video['duration_seconds'] / 60, 2)
        
        return jsonify(videos)
    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>')
@login_required
def api_get_video(video_id):
    """Get specific video details"""
    try:
        video = db.get_saved_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        video['file_exists'] = os.path.exists(video['file_path'])
        video['file_size_mb'] = round(video['file_size_bytes'] / (1024 * 1024), 2)
        video['duration_minutes'] = round(video['duration_seconds'] / 60, 2)
        
        return jsonify(video)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>/download')
@login_required
def api_download_video(video_id):
    """Download a specific video file"""
    try:
        video = db.get_saved_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        if not os.path.exists(video['file_path']):
            return jsonify({'error': 'Video file not found on disk'}), 404
        
        from flask import send_file
        return send_file(video['file_path'], as_attachment=True, download_name=video['filename'])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>', methods=['DELETE'])
@login_required
def api_delete_video(video_id):
    """Soft-delete a specific video and move to trash"""
    try:
        video = db.get_saved_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # Keep file on disk; mark as deleted in DB
        success = db.delete_saved_video(video_id)
        if success:
            return jsonify({'success': True, 'message': 'Video moved to Trash'})
        else:
            return jsonify({'error': 'Failed to delete video from database'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/delete_all', methods=['DELETE'])
@login_required
def api_delete_all_videos():
    """Soft-delete all videos"""
    try:
        db_count = db.delete_all_saved_videos()
        return jsonify({'success': True, 'message': f'Moved {db_count} videos to Trash'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/trash')
@login_required
def api_get_trash():
    """Get soft-deleted videos (trash) with optional filters"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        search_time = request.args.get('search_time')
        videos = db.get_deleted_videos(start_date, end_date, search_time)
        for video in videos:
            video['file_exists'] = os.path.exists(video['file_path'])
            video['file_size_mb'] = round(video['file_size_bytes'] / (1024 * 1024), 2)
            video['duration_minutes'] = round(video['duration_seconds'] / 60, 2)
        return jsonify(videos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>/recover', methods=['POST'])
@login_required
def api_recover_video(video_id):
    """Recover a soft-deleted video"""
    try:
        success = db.recover_video(video_id)
        if success:
            return jsonify({'success': True, 'message': 'Video recovered'})
        return jsonify({'error': 'Video not found or not deleted'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>/delete_permanent', methods=['DELETE'])
@login_required
def api_delete_permanent(video_id):
    """Permanently delete a soft-deleted video (also remove file)"""
    try:
        video = db.get_saved_video_by_id(video_id)
        if not video or not video.get('is_deleted'):
            return jsonify({'error': 'Video not found in Trash'}), 404
        if os.path.exists(video['file_path']):
            try:
                os.remove(video['file_path'])
            except Exception as e:
                print(f"File delete error for {video['file_path']}: {e}")
        success = db.delete_permanently(video_id)
        if success:
            return jsonify({'success': True, 'message': 'Video deleted permanently'})
        return jsonify({'error': 'Failed to delete permanently'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def purge_trash_background(days: int = 30):
    """Background task to purge soft-deleted videos older than N days"""
    while True:
        try:
            ids = db.purge_deleted_older_than_days(days)
            if ids:
                print(f"Purged {len(ids)} videos from Trash (>{days} days)")
            time.sleep(3600)  # hourly
        except Exception as e:
            print(f"Error purging trash: {e}")
            time.sleep(3600)

@app.route('/api/videos/recording/start', methods=['POST'])
@login_required
def api_start_recording():
    """Start video recording"""
    try:
        if video_recorder.is_recording_active():
            return jsonify({'error': 'Recording is already in progress'}), 400
        
        video_recorder.start_recording()
        return jsonify({'success': True, 'message': 'Recording started'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/recording/stop', methods=['POST'])
@login_required
def api_stop_recording():
    """Stop video recording"""
    try:
        if not video_recorder.is_recording_active():
            return jsonify({'error': 'No recording in progress'}), 400
        
        video_recorder.stop_recording()
        return jsonify({'success': True, 'message': 'Recording stopped'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/recording/status')
@login_required
def api_recording_status():
    """Get current recording status"""
    try:
        is_recording = video_recorder.is_recording_active()
        info = video_recorder.get_recording_info() if is_recording else None
        
        return jsonify({
            'is_recording': is_recording,
            'recording_info': info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/pinned')
@login_required
def api_get_pinned_videos():
    """Get all pinned videos"""
    try:
        videos = db.get_pinned_videos()
        
        # Add file existence check and format file sizes
        for video in videos:
            video['file_exists'] = os.path.exists(video['file_path'])
            video['file_size_mb'] = round(video['file_size_bytes'] / (1024 * 1024), 2)
            video['duration_minutes'] = round(video['duration_seconds'] / 60, 2)
        
        return jsonify(videos)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>/pin', methods=['POST'])
@login_required
def api_pin_video(video_id):
    """Pin a video as important"""
    try:
        # Check if video exists first
        video = db.get_saved_video_by_id(video_id)
        if not video:
            return jsonify({'error': 'Video not found'}), 404
        
        # Check if already pinned
        if video.get('is_pinned', 0) == 1:
            return jsonify({'error': 'Video is already pinned'}), 400
        
        success = db.pin_video(video_id)
        if success:
            return jsonify({'success': True, 'message': 'Video pinned successfully'})
        else:
            return jsonify({'error': 'Failed to pin video'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/videos/<int:video_id>/unpin', methods=['POST'])
@login_required
def api_unpin_video(video_id):
    """Unpin a video"""
    try:
        success = db.unpin_video(video_id)
        if success:
            return jsonify({'success': True, 'message': 'Video unpinned successfully'})
        else:
            return jsonify({'error': 'Video not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -----------------------------
# Client Management API
# -----------------------------
@app.route('/api/clients')
@login_required
def api_get_clients():
    """Get all clients"""
    try:
        clients = db.get_all_clients()
        return jsonify(clients)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clients/<int:client_id>')
@login_required
def api_get_client(client_id):
    """Get client by ID"""
    try:
        client = db.get_client_by_id(client_id)
        if client:
            return jsonify(client)
        else:
            return jsonify({'error': 'Client not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clients', methods=['POST'])
@login_required
def api_add_client():
    """Add new client"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'nrc_number', 'contact', 'number_plate']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        # Check for duplicate NRC or number plate
        existing_nrc = db.get_client_by_nrc(data['nrc_number'])
        if existing_nrc:
            return jsonify({'error': 'Client with this NRC number already exists'}), 400
        
        existing_plate = db.get_client_by_plate(data['number_plate'])
        if existing_plate:
            return jsonify({'error': 'Client with this number plate already exists'}), 400
        
        client_id = db.add_client(data)
        return jsonify({'success': True, 'client_id': client_id, 'message': 'Client added successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clients/<int:client_id>', methods=['PUT'])
@login_required
def api_update_client(client_id):
    """Update client"""
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['name', 'nrc_number', 'contact', 'number_plate']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'error': f'{field} is required'}), 400
        
        # Check if client exists
        existing_client = db.get_client_by_id(client_id)
        if not existing_client:
            return jsonify({'error': 'Client not found'}), 404
        
        # Check for duplicate NRC or number plate (excluding current client)
        existing_nrc = db.get_client_by_nrc(data['nrc_number'])
        if existing_nrc and existing_nrc['id'] != client_id:
            return jsonify({'error': 'Another client with this NRC number already exists'}), 400
        
        existing_plate = db.get_client_by_plate(data['number_plate'])
        if existing_plate and existing_plate['id'] != client_id:
            return jsonify({'error': 'Another client with this number plate already exists'}), 400
        
        success = db.update_client(client_id, data)
        if success:
            return jsonify({'success': True, 'message': 'Client updated successfully'})
        else:
            return jsonify({'error': 'Failed to update client'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clients/<int:client_id>', methods=['DELETE'])
@login_required
def api_delete_client(client_id):
    """Delete client"""
    try:
        success = db.delete_client(client_id)
        if success:
            return jsonify({'success': True, 'message': 'Client deleted successfully'})
        else:
            return jsonify({'error': 'Client not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/clients/search')
@login_required
def api_search_clients():
    """Search clients"""
    try:
        search_term = request.args.get('q', '')
        if not search_term:
            return jsonify([])
        
        clients = db.search_clients(search_term)
        return jsonify(clients)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -----------------------------
# Socket.IO events
# -----------------------------
@socketio.on('connect')
def on_connect():
    emit('status', {
        'connected': True,
        'processes': {k: v.is_running() for k, v in processes.items()}
    })


@socketio.on('start_live')
def on_start_live():
    live_streamer.start()
    emit('live_status', {'running': True})


@socketio.on('stop_live')
def on_stop_live():
    print("Stopping live stream...")
    live_streamer.stop()
    emit('live_status', {'running': False})


@socketio.on('start_calibration')
def on_start_calibration():
    processes['calibration'].start()
    global CURRENT_POLY
    CURRENT_POLY = []
    emit('calibration_status', {'running': True})


@socketio.on('stop_calibration')
def on_stop_calibration():
    print("Stopping calibration...")
    processes['calibration'].stop()
    emit('calibration_status', {'running': False})


@socketio.on('calibration_click')
def on_calibration_click(data):
    # data: {x:int, y:int}
    try:
        x = int(data.get('x'))
        y = int(data.get('y'))
        CURRENT_POLY.append([x, y])
        socketio.emit('calibration_update', {
            'current': CURRENT_POLY,
            'slots': CALIBRATION_SLOTS,
        })
    except Exception as e:
        emit('calibration_error', {'error': str(e)})


@socketio.on('calibration_close_slot')
def on_calibration_close_slot(data):
    # data: {name?: str}
    global CURRENT_POLY
    if len(CURRENT_POLY) >= 3:
        slot = {
            'id': len(CALIBRATION_SLOTS) + 1,
            'name': data.get('name') or f'Slot {len(CALIBRATION_SLOTS)+1}',
            'points': CURRENT_POLY[:],
        }
        CALIBRATION_SLOTS.append(slot)
        CURRENT_POLY = []
        socketio.emit('calibration_update', {'current': CURRENT_POLY, 'slots': CALIBRATION_SLOTS})


@socketio.on('calibration_delete_last_point')
def on_calibration_delete_last_point():
    if CURRENT_POLY:
        CURRENT_POLY.pop()
        socketio.emit('calibration_update', {'current': CURRENT_POLY, 'slots': CALIBRATION_SLOTS})


@socketio.on('calibration_delete_last_slot')
def on_calibration_delete_last_slot():
    if CALIBRATION_SLOTS:
        CALIBRATION_SLOTS.pop()
        socketio.emit('calibration_update', {'current': CURRENT_POLY, 'slots': CALIBRATION_SLOTS})


@socketio.on('calibration_save')
def on_calibration_save():
    persist_slots()


@socketio.on('start_detection')
def on_start_detection():
    processes['detection'].start()
    emit('detection_status', {'running': True})


@socketio.on('stop_detection')
def on_stop_detection():
    print("Stopping detection...")
    processes['detection'].stop()
    emit('detection_status', {'running': False})


@socketio.on('start_viewer')
def on_start_viewer():
    processes['viewer'].start()
    emit('viewer_status', {'running': True})


@socketio.on('stop_viewer')
def on_stop_viewer():
    print("Stopping viewer...")
    processes['viewer'].stop()
    emit('viewer_status', {'running': False})


@socketio.on('calibration_stopped')
def on_calibration_stopped():
    print("Calibration script stopped by user (q key)")
    # Update the process status to reflect that it's no longer running
    processes['calibration'].proc = None
    emit('calibration_status', {'running': False})


@socketio.on('detection_stopped')
def on_detection_stopped():
    print("Detection script stopped by user (q key)")
    # Update the process status to reflect that it's no longer running
    processes['detection'].proc = None
    emit('detection_status', {'running': False})


@socketio.on('viewer_stopped')
def on_viewer_stopped():
    print("Viewer script stopped by user (q key)")
    # Update the process status to reflect that it's no longer running
    processes['viewer'].proc = None
    emit('viewer_status', {'running': False})


@socketio.on('start_video_recording')
def on_start_video_recording():
    """Start video recording via Socket.IO"""
    try:
        if video_recorder.is_recording_active():
            emit('video_recording_error', {'error': 'Recording is already in progress'})
            return
        
        video_recorder.start_recording()
        emit('video_recording_status', {'recording': True, 'message': 'Recording started'})
    except Exception as e:
        emit('video_recording_error', {'error': str(e)})


@socketio.on('stop_video_recording')
def on_stop_video_recording():
    """Stop video recording via Socket.IO"""
    try:
        if not video_recorder.is_recording_active():
            emit('video_recording_error', {'error': 'No recording in progress'})
            return
        
        video_recorder.stop_recording()
        emit('video_recording_status', {'recording': False, 'message': 'Recording stopped'})
    except Exception as e:
        emit('video_recording_error', {'error': str(e)})


@socketio.on('get_video_recording_status')
def on_get_video_recording_status():
    """Get current video recording status via Socket.IO"""
    try:
        is_recording = video_recorder.is_recording_active()
        info = video_recorder.get_recording_info() if is_recording else None
        
        emit('video_recording_status', {
            'recording': is_recording,
            'recording_info': info
        })
    except Exception as e:
        emit('video_recording_error', {'error': str(e)})

@socketio.on('calibration_update')
def handle_calibration_update(data):
    """Handle calibration update from Python client"""
    try:
        print(f"Received calibration update: {data}")
        # Broadcast to all connected clients
        emit('calibration_update', data, broadcast=True)
    except Exception as e:
        print(f"Error handling calibration update: {e}")

@socketio.on('detection_update')
def handle_detection_update(data):
    """Handle individual slot detection update from Python client"""
    try:
        print(f"Received detection update: {data}")
        # Broadcast to all connected clients
        emit('detection_update', data, broadcast=True)
    except Exception as e:
        print(f"Error handling detection update: {e}")

@socketio.on('occupancy_update')
def handle_occupancy_update(data):
    """Handle overall occupancy update from Python client"""
    try:
        print(f"Received occupancy update: {data}")
        # Broadcast to all connected clients
        emit('occupancy_update', data, broadcast=True)
    except Exception as e:
        print(f"Error handling occupancy update: {e}")


# -----------------------------
# Background Tasks
# -----------------------------
def cleanup_expired_reservations():
    """Background task to clean up expired reservations"""
    while True:
        try:
            db.cleanup_expired_reservations()
            time.sleep(300)  # Run every 5 minutes instead of every minute
        except Exception as e:
            print(f"Error in cleanup task: {e}")
            time.sleep(300)

def process_fifo_requests():
    """Background task to automatically process FIFO requests"""
    while True:
        try:
            fifo_enabled = db.get_setting('fifo_enabled') == 'true'
            if fifo_enabled:
                pending_requests = db.get_pending_requests()
                if pending_requests:
                    # Process the oldest request
                    oldest_request = pending_requests[0]
                    stats = db.get_occupancy_stats()
                    
                    if stats['free_slots'] > 0:
                        # Accept the request
                        reservation_id = db.accept_request(oldest_request['request_id'])
                        if not reservation_id:
                            time.sleep(30)
                            continue
                        print(f"Auto-accepted request {oldest_request['request_id']} via FIFO")
                        
                        # Emit update to connected clients
                        socketio.emit('request_processed', {
                            'request_id': oldest_request['request_id'],
                            'action': 'accepted',
                            'method': 'fifo'
                        })
            
            time.sleep(30)  # Check every 30 seconds
        except Exception as e:
            print(f"Error in FIFO processing: {e}")
            time.sleep(30)

def update_slot_status_from_detection():
    """Background task to update slot status from prototype_client.py detection results"""
    while True:
        try:
            # This would integrate with the detection results from prototype_client.py
            # For now, we'll just emit occupancy updates
            stats = db.get_occupancy_stats()
            socketio.emit('occupancy_update', stats)
            time.sleep(5)  # Update every 5 seconds
        except Exception as e:
            print(f"Error updating slot status: {e}")
            time.sleep(5)

def auto_video_recording():
    """Background task for automatic 24-hour video recording segments"""
    last_day = None
    current_recording = None
    
    while True:
        try:
            current_time = datetime.now()
            current_day = current_time.date()
            
            # Check if we need to start a new day's recording
            if last_day != current_day:
                # Stop previous recording if it exists
                if current_recording and video_recorder.is_recording_active():
                    print("Stopping previous day's recording...")
                    video_recorder.stop_recording()
                    current_recording = None
                
                # Start new day's recording
                print(f"Starting new day's recording: {current_day}")
                video_recorder.start_recording()
                current_recording = current_day
                last_day = current_day
            
            # Check if we need to stop recording (end of day)
            if current_recording and current_time.hour == 23 and current_time.minute >= 59:
                if video_recorder.is_recording_active():
                    print("Stopping end-of-day recording...")
                    video_recorder.stop_recording()
                    current_recording = None
            
            time.sleep(60)  # Check every minute
        except Exception as e:
            print(f"Error in auto video recording: {e}")
            time.sleep(60)

# Start background tasks
cleanup_thread = threading.Thread(target=cleanup_expired_reservations, daemon=True)
cleanup_thread.start()

fifo_thread = threading.Thread(target=process_fifo_requests, daemon=True)
fifo_thread.start()

status_thread = threading.Thread(target=update_slot_status_from_detection, daemon=True)
status_thread.start()

auto_recording_thread = threading.Thread(target=auto_video_recording, daemon=True)
auto_recording_thread.start()

trash_purge_thread = threading.Thread(target=purge_trash_background, daemon=True)
trash_purge_thread.start()


def open_browser():
    """Open browser after a short delay"""
    time.sleep(2)
    webbrowser.open('http://localhost:5000')

def print_startup_info():
    """Print startup information and system details"""
    print("=" * 60)
    print("ENHANCED PARKING MANAGEMENT SYSTEM")
    print("=" * 60)
    print()
    print("Starting Flask application...")
    print("Admin Dashboard: http://localhost:5000")
    print("Client Portal: http://localhost:5000/client")
    print("Controls: http://localhost:5000/controls")
    print()
    print("Default Admin Login:")
    print("   Username: admin")
    print("   Password: admin123")
    print()
    print("Features:")
    print("- Admin authentication and dashboard")
    print("- Real-time parking slot management")
    print("- Reservation request processing")
    print("- FIFO automatic processing")
    print("- Client reservation portal")
    print("- ESP32-CAM integration")
    print("- Python script controls")
    print()
    print("=" * 60)

def main():
    """Main application entry point with enhanced startup"""
    # Print startup information
    print_startup_info()
    
    # Ensure python-client exists for optional controls and detection scripts
    if not os.path.isdir(PYTHON_CLIENT_DIR):
        print(f"Warning: python-client directory not found at {PYTHON_CLIENT_DIR}")
        print("Optional live/detection/calibration controls will be disabled.")
    
    # Open browser in a separate thread
    browser_thread = threading.Thread(target=open_browser)
    browser_thread.daemon = True
    browser_thread.start()
    
    @app.route('/controls')
    @login_required
    def controls():
        """Controls page for calibration"""
        return render_template('controls.html')

    # Start the Flask-SocketIO application
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=False)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    except Exception as e:
        print(f"\nError starting application: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()



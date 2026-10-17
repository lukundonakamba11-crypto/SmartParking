import os
import sys
import threading
import time
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
from flask_socketio import SocketIO, emit
import socket
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

# Add the admin portal directory to the path to import shared database manager
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'admin_site'))
from database import DatabaseManager

# -----------------------------
# Configuration
# -----------------------------
app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('CLIENT_PORTAL_SECRET_KEY', 'development-only-change-this-secret')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

# Initialize database - use the shared admin portal database
db = DatabaseManager(os.path.join(os.path.dirname(__file__), '..', 'admin_site', 'parking_system.db'))

# -----------------------------
# Authentication Functions
# -----------------------------
from functools import wraps

def authenticate_client(number_plate: str, password: str) -> bool:
    """Authenticate a client, migrating legacy plate-password accounts on login."""
    number_plate = number_plate.strip().upper()
    client = db.get_client_by_plate(number_plate)
    if not client:
        return False
    stored_hash = client.get('password_hash')
    if stored_hash:
        return check_password_hash(stored_hash, password)
    if password.strip() != number_plate:
        return False
    db.update_client_password(client['id'], generate_password_hash(password))
    return True


def send_password_reset_email(recipient: str, reset_url: str) -> bool:
    """Send a reset link when SMTP is configured; never expose the token in the UI."""
    host = os.environ.get('PARKING_SMTP_HOST')
    sender = os.environ.get('PARKING_SMTP_FROM')
    if not host or not sender:
        app.logger.warning('Password reset requested but SMTP is not configured.')
        return False

    message = EmailMessage()
    message['Subject'] = 'Reset your Smart Parking password'
    message['From'] = sender
    message['To'] = recipient
    message.set_content(f'Use this link within one hour to reset your password:\n\n{reset_url}')
    port = int(os.environ.get('PARKING_SMTP_PORT', '587'))
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        smtp_user = os.environ.get('PARKING_SMTP_USER')
        smtp_password = os.environ.get('PARKING_SMTP_PASSWORD')
        if smtp_user and smtp_password:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)
    return True


def login_required(f):
    """Decorator to require client login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('client_logged_in'):
            return redirect(url_for('client_login'))
        return f(*args, **kwargs)
    return decorated_function

# -----------------------------
# Routes
# -----------------------------
@app.route('/')
def index():
    """Redirect to free parking page for non-logged in users"""
    return redirect(url_for('free_parking'))

@app.route('/free-parking')
def free_parking():
    """Free parking space page - shows available slots without login"""
    return render_template('free_parking.html')

@app.route('/login', methods=['GET', 'POST'])
def client_login():
    """Client login page"""
    if request.method == 'POST':
        number_plate = request.form.get('number_plate', '').strip().upper()
        password = request.form.get('password', '').strip()
        
        if authenticate_client(number_plate, password):
            client = db.get_client_by_plate(number_plate)
            session.clear()
            session['client_logged_in'] = True
            session['client_id'] = client['id']
            session['client_number_plate'] = number_plate
            session['client_name'] = client['name'] if client else number_plate
            session['client_email'] = client.get('email') if client else None
            
            return redirect(url_for('client_dashboard'))
        else:
            flash('Invalid number plate or password. Please try again.', 'error')
    
    return render_template('client_login.html')

@app.route('/logout')
def client_logout():
    """Client logout"""
    session.clear()
    return redirect(url_for('free_parking'))

@app.route('/dashboard')
@login_required
def client_dashboard():
    """Client dashboard after login"""
    return render_template('client_dashboard.html')

@app.route('/register', methods=['GET', 'POST'])
def client_register():
    """Client registration page"""
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        nrc_number = request.form.get('nrc_number', '').strip()
        contact = request.form.get('contact', '').strip()
        email = request.form.get('email', '').strip()
        number_plate = request.form.get('number_plate', '').strip().upper()
        password = request.form.get('password', '')
        
        # Validate required fields
        if not all([name, nrc_number, contact, number_plate, password]):
            flash('Please fill in all required fields.', 'error')
            return render_template('client_register.html')
        if len(password) < 8:
            flash('Choose a password with at least 8 characters.', 'error')
            return render_template('client_register.html')

        # Check for duplicate number plate or NRC in the shared database
        if db.get_client_by_plate(number_plate):
            flash('This number plate is already registered. Please login or use a different vehicle.', 'error')
            return render_template('client_register.html')
        if db.get_client_by_nrc(nrc_number):
            flash('This NRC number is already registered. Please login or contact support.', 'error')
            return render_template('client_register.html')
        
        client_data = {
            'name': name,
            'nrc_number': nrc_number,
            'contact': contact,
            'email': email,
            'number_plate': number_plate,
            'vehicle_type': request.form.get('vehicle_type', '').strip(),
            'vehicle_color': request.form.get('vehicle_color', '').strip(),
        }

        try:
            client_data['password_hash'] = generate_password_hash(password)
            client_id = db.add_client(client_data)
        except Exception as e:
            flash(f'Unable to save registration: {e}', 'error')
            return render_template('client_register.html')

        session['client_logged_in'] = True
        session['client_id'] = client_id
        session['client_number_plate'] = number_plate
        session['client_name'] = name
        session['client_email'] = email

        flash('Registration successful! You are now logged in.', 'success')
        return redirect(url_for('client_dashboard'))
    
    return render_template('client_register.html')


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Send a signed, time-limited reset link to a registered email address."""
    if request.method == 'POST':
        number_plate = request.form.get('number_plate', '').strip().upper()
        client = db.get_client_by_plate(number_plate)
        if client and client.get('email'):
            serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
            token = serializer.dumps(client['id'], salt='parking-password-reset')
            reset_url = url_for('reset_password', token=token, _external=True)
            try:
                send_password_reset_email(client['email'], reset_url)
            except Exception:
                app.logger.exception('Unable to send password reset email.')
        flash('If the account has a registered email and mail delivery is configured, a reset link will be sent.', 'info')
        return redirect(url_for('client_login'))
    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Validate a one-hour reset token and update the account password."""
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        client_id = serializer.loads(token, salt='parking-password-reset', max_age=3600)
    except (BadSignature, SignatureExpired):
        flash('This password reset link is invalid or has expired.', 'error')
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        if len(password) < 8:
            flash('Choose a password with at least 8 characters.', 'error')
            return render_template('reset_password.html')
        if db.update_client_password(client_id, generate_password_hash(password)):
            flash('Password updated. You can now sign in.', 'success')
            return redirect(url_for('client_login'))
        flash('Account not found.', 'error')
        return redirect(url_for('forgot_password'))
    return render_template('reset_password.html')


@app.route('/profile', methods=['GET', 'POST'])
@login_required
def client_profile():
    """View and update contact and vehicle information for the signed-in client."""
    client = db.get_client_by_id(session['client_id'])
    if not client:
        session.clear()
        return redirect(url_for('client_login'))
    if request.method == 'POST':
        updated = {
            'name': request.form.get('name', '').strip(),
            'nrc_number': client['nrc_number'],
            'contact': request.form.get('contact', '').strip(),
            'email': request.form.get('email', '').strip(),
            'number_plate': request.form.get('number_plate', '').strip().upper(),
            'vehicle_type': request.form.get('vehicle_type', '').strip(),
            'vehicle_color': request.form.get('vehicle_color', '').strip(),
        }
        if not all([updated['name'], updated['contact'], updated['number_plate']]):
            flash('Name, contact, and number plate are required.', 'error')
            return render_template('client_profile.html', client=client)
        existing = db.get_client_by_plate(updated['number_plate'])
        if existing and existing['id'] != client['id']:
            flash('That number plate is already linked to another account.', 'error')
            return render_template('client_profile.html', client=client)
        new_password = request.form.get('new_password', '')
        if new_password:
            if len(new_password) < 8 or not check_password_hash(
                client['password_hash'], request.form.get('current_password', '')
            ):
                flash('Enter your current password and a new password of at least 8 characters.', 'error')
                return render_template('client_profile.html', client=client)
            db.update_client_password(client['id'], generate_password_hash(new_password))
        db.update_client(client['id'], updated)
        session['client_name'] = updated['name']
        session['client_number_plate'] = updated['number_plate']
        session['client_email'] = updated['email']
        flash('Profile updated successfully.', 'success')
        return redirect(url_for('client_profile'))
    return render_template('client_profile.html', client=client)

# -----------------------------
# API Routes
# -----------------------------
@app.route('/api/availability')
def api_availability():
    """Get parking availability for public view"""
    try:
        stats = db.get_occupancy_stats()
        return jsonify({
            'available': stats['free_slots'] > 0,
            'free_slots': stats['free_slots'],
            'total_slots': stats['total_slots'],
            'occupied_slots': stats['occupied_slots'],
            'occupancy_rate': round(stats['occupancy_rate'], 1),
            'slot_predictions': get_slot_predictions(stats),
            'message': 'Parking available' if stats['free_slots'] > 0 else 'Parking lot full'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/lan-url')
def api_lan_url():
    """Return a best-effort LAN URL for QR code use on mobile devices."""
    try:
        # Try to get a LAN IP bound to an active interface
        hostname = socket.gethostname()
        lan_ip = socket.gethostbyname(hostname)
        scheme = 'http'
        port = 5001
        return jsonify({
            'ok': True,
            'url': f"{scheme}://{lan_ip}:{port}/"
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


def get_slot_predictions(stats: dict) -> dict:
    """Generate slot usage predictions based on historical reservations."""
    total = stats.get('total_slots', 0) or 0
    if total <= 0:
        return {'most_used': [], 'most_free': []}

    try:
        reservations = db.get_all_reservations()
    except Exception:
        reservations = []

    now = datetime.now()
    slot_usage = {}
    slot_last_used = {}

    for reservation in reservations:
        slot_id = reservation.get('slot_id')
        if slot_id is None:
            continue

        # Weight recent reservations more heavily.
        try:
            start_time = datetime.fromisoformat(str(reservation.get('start_time')))
        except Exception:
            start_time = now

        age_days = max((now - start_time).days, 0) + 1
        weight = 1.0 / age_days

        slot_usage[slot_id] = slot_usage.get(slot_id, 0.0) + weight
        slot_last_used[slot_id] = max(slot_last_used.get(slot_id, now), start_time)

    # Build slot labels and sort by usage frequency
    if slot_usage:
        most_used_slots = sorted(slot_usage.items(), key=lambda x: (-x[1], x[0]))
        most_used = [f"Slot {slot_id}" for slot_id, _ in most_used_slots[:5]]

        all_slot_ids = set(range(1, total + 1))
        used_slot_ids = set(slot_usage.keys())
        unused_slot_ids = sorted(all_slot_ids - used_slot_ids)

        if len(unused_slot_ids) >= 5:
            most_free = [f"Slot {slot_id}" for slot_id in unused_slot_ids[:5]]
        else:
            least_used_slots = sorted(slot_usage.items(), key=lambda x: (x[1], x[0]))
            most_free = [f"Slot {slot_id}" for slot_id, _ in least_used_slots[:max(0, 5 - len(unused_slot_ids))]]
            most_free = [f"Slot {slot_id}" for slot_id in unused_slot_ids] + most_free

        if len(most_free) == 0:
            most_free = [f"Slot {i}" for i in range(max(1, total - 4), total + 1)]
    else:
        most_used = [f"Slot {i}" for i in range(1, min(6, total + 1))]
        most_free = [f"Slot {i}" for i in range(max(1, total - 4), total + 1)]

    return {
        'most_used': most_used,
        'most_free': most_free
    }

@app.route('/api/client/availability')
@login_required
def api_client_availability():
    """Get parking availability for logged-in clients"""
    try:
        stats = db.get_occupancy_stats()
        
        # Get next available time if all slots are occupied
        next_available_time = None
        if stats['free_slots'] <= 0:
            # Use the advanced algorithm to find next available time
            from datetime import datetime, timedelta
            now = datetime.now()
            next_available = db.find_next_available_time(now, 1)  # 1 hour duration
            if next_available:
                next_available_time = next_available.isoformat()
        
        return jsonify({
            'available': stats['free_slots'] > 0,
            'free_slots': stats['free_slots'],
            'total_slots': stats['total_slots'],
            'occupied_slots': stats['occupied_slots'],
            'occupancy_rate': round(stats['occupancy_rate'], 1),
            'next_available_time': next_available_time,
            'slot_predictions': get_slot_predictions(stats),
            'message': 'Parking available' if stats['free_slots'] > 0 else 'Parking lot full'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/reserve', methods=['POST'])
@login_required
def api_client_reserve():
    """Create a reservation request from logged-in client"""
    try:
        data = request.get_json(silent=True) or {}
        
        # Validate required fields
        required_fields = ['name', 'number_plate', 'start_time', 'duration']
        for field in required_fields:
            if field not in data:
                return jsonify({'error': f'Missing required field: {field}'}), 400
        
        # Parse time and duration
        start_time = str(data['start_time'])
        duration_hours = int(data['duration'])
        if duration_hours < 1 or duration_hours > 24:
            return jsonify({'error': 'Duration must be between 1 and 24 hours.'}), 400
        parsed_start = datetime.fromisoformat(start_time)
        if parsed_start <= datetime.now():
            return jsonify({'error': 'Reservation start time must be in the future.'}), 400

        client = db.get_client_by_id(session['client_id'])
        if not client:
            return jsonify({'error': 'Client account not found.'}), 404
        
        # Check availability using the new algorithm
        availability = db.check_reservation_availability(start_time, duration_hours)
        
        if not availability['can_reserve']:
            if availability['next_available_time']:
                next_time = datetime.fromisoformat(availability['next_available_time'])
                return jsonify({
                    'error': 'No slots available at that time',
                    'next_available_time': availability['next_available_time'],
                    'message': f'All {availability["total_slots"]} slots occupied. Next available at {next_time.strftime("%Y-%m-%d %H:%M")}',
                    'suggested_start_time': availability['next_available_time']
                }), 400
            else:
                return jsonify({
                    'error': 'No slots available',
                    'message': f'All {availability["total_slots"]} slots are currently occupied'
                }), 400
        
        # Get client data from session
        client_data = {
            key: client.get(key)
            for key in ('name', 'nrc_number', 'contact', 'email', 'number_plate', 'vehicle_type', 'vehicle_color')
        }

        # Create reservation request with client data
        request_data = {
            'client_id': client['id'],
            'name': client['name'],
            'number_plate': client['number_plate'],
            'start_time': start_time,
            'duration': duration_hours,
            'payment_status': 'pending',
            'client_data': client_data
        }
        request_id = db.add_reservation_request(request_data)
        
        return jsonify({
            'success': True,
            'request_id': request_id,
            'message': f'Reservation request submitted successfully. {availability["available_slots"]} slots available.',
            'availability': availability
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/request/<int:request_id>/status')
@login_required
def api_client_request_status(request_id):
    """Get status of a reservation request"""
    try:
        requests = db.get_client_requests(session['client_id'], session['client_number_plate'])
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

@app.route('/api/client/payment', methods=['POST'])
@login_required
def api_client_payment():
    """Process payment for reservation"""
    try:
        data = request.get_json(silent=True) or {}
        request_id = data.get('request_id')
        payment_method = data.get('payment_method', 'cash').lower()
        
        if not request_id:
            return jsonify({'error': 'Request ID is required'}), 400
        
        # Look up only this account's request.
        requests = db.get_client_requests(session['client_id'], session['client_number_plate'])
        request_data = next((req for req in requests if req['request_id'] == request_id), None)
        
        if not request_data:
            return jsonify({'error': 'Request not found'}), 404
        
        if payment_method not in {'cash', 'card', 'mobile_money'}:
            return jsonify({'error': 'Unsupported payment method.'}), 400
        if payment_method in {'card', 'mobile_money'}:
            return jsonify({
                'error': f'{payment_method.replace("_", " ").title()} payments are unavailable until a payment provider is configured.',
                'payment_status': 'not_configured'
            }), 503
        hourly_rate = float(db.get_setting('default_hourly_rate') or 5.0)
        amount = round(int(request_data['duration']) * hourly_rate, 2)
        payment_id = db.add_payment(request_id, amount, payment_method, 'pending')
        
        return jsonify({
            'success': True,
            'payment_id': payment_id,
            'amount': amount,
            'payment_method': payment_method,
            'payment_status': 'pending',
            'message': 'Cash payment recorded and awaiting staff verification. No online payment was captured.'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/check-availability', methods=['POST'])
@login_required
def api_check_availability():
    """Check availability for a specific time and duration"""
    try:
        data = request.get_json()
        
        if not data or 'start_time' not in data or 'duration' not in data:
            return jsonify({'error': 'Missing start_time or duration'}), 400
        
        start_time = data['start_time']
        duration_hours = int(data['duration'])
        
        # Check availability using the new algorithm
        availability = db.check_reservation_availability(start_time, duration_hours)
        
        return jsonify({
            'success': True,
            'availability': availability
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/reservation-timeline')
@login_required
def api_reservation_timeline():
    """Get reservation timeline for visualization"""
    try:
        timeline = db.get_reservation_timeline()
        return jsonify({
            'success': True,
            'timeline': timeline
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/client/reservations')
@login_required
def api_client_reservations():
    """Get active reservations for logged-in client"""
    try:
        client_reservations = db.get_client_reservations(session['client_id'])
        return jsonify({
            'success': True,
            'reservations': client_reservations
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/client/history')
@login_required
def api_client_history():
    """Return this client's reservation and payment history."""
    return jsonify({
        'reservations': db.get_client_reservations(session['client_id']),
        'requests': db.get_client_requests(session['client_id'], session['client_number_plate']),
        'payments': db.get_client_payments(session['client_id']),
    })


@app.route('/api/client/reservations/<int:reservation_id>', methods=['PUT'])
@login_required
def api_client_update_reservation(reservation_id):
    data = request.get_json(silent=True) or {}
    try:
        start_time = str(data.get('start_time', ''))
        duration = int(data.get('duration', 0))
        start = datetime.fromisoformat(start_time)
        if start <= datetime.now() or duration < 1 or duration > 24:
            return jsonify({'error': 'Choose a future start time and a duration from 1 to 24 hours.'}), 400
        updated = db.update_client_reservation(reservation_id, session['client_id'], start_time, duration)
        if not updated:
            return jsonify({'error': 'Reservation cannot be changed; it may have started or no space is available.'}), 409
        return jsonify({'success': True, 'message': 'Reservation updated.'})
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid start time or duration.'}), 400


@app.route('/api/client/reservations/<int:reservation_id>/cancel', methods=['POST'])
@login_required
def api_client_cancel_reservation(reservation_id):
    if not db.cancel_client_reservation(reservation_id, session['client_id']):
        return jsonify({'error': 'Reservation cannot be cancelled after it starts or is checked in.'}), 409
    return jsonify({'success': True, 'message': 'Reservation cancelled.'})


@app.route('/api/client/reservations/<int:reservation_id>/check-in', methods=['POST'])
@login_required
def api_client_check_in(reservation_id):
    if not db.check_in_reservation(reservation_id, session['client_id']):
        return jsonify({'error': 'Check-in is unavailable. Confirm the reservation and arrival window.'}), 409
    return jsonify({'success': True, 'message': 'Check-in recorded.'})


@app.route('/api/client/check-in', methods=['POST'])
@login_required
def api_client_check_in_code():
    data = request.get_json(silent=True) or {}
    code = str(data.get('reservation_code', '')).strip().upper()
    reservation = next((
        row for row in db.get_client_reservations(session['client_id'])
        if row.get('reservation_code') and row['reservation_code'].upper() == code
    ), None)
    if not reservation:
        return jsonify({'error': 'Reservation code not found.'}), 404
    return api_client_check_in(reservation['reservation_id'])


@app.route('/api/client/reservations/<int:reservation_id>/check-out', methods=['POST'])
@login_required
def api_client_check_out(reservation_id):
    amount = db.check_out_reservation(reservation_id, session['client_id'])
    if amount is None:
        return jsonify({'error': 'Reservation is not active or does not belong to this account.'}), 404
    return jsonify({
        'success': True,
        'amount_due': amount,
        'payment_status': 'pending',
        'message': 'Parking ended. The fee is calculated from check-in time; payment is pending.'
    })


@app.route('/api/client/reservations/<int:reservation_id>/feedback', methods=['POST'])
@login_required
def api_client_feedback(reservation_id):
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get('rating', 0))
    except (TypeError, ValueError):
        rating = 0
    comment = str(data.get('comment', '')).strip()[:1000]
    if rating < 1 or rating > 5:
        return jsonify({'error': 'Rating must be between 1 and 5.'}), 400
    if not db.save_reservation_feedback(reservation_id, session['client_id'], rating, comment):
        return jsonify({'error': 'Feedback is available only for your completed reservations.'}), 409
    return jsonify({'success': True, 'message': 'Thank you for your feedback.'})

@app.route('/api/client/slot-stats')
@login_required
def api_slot_stats():
    """Get detailed slot utilization statistics"""
    try:
        stats = db.get_slot_utilization_stats()
        return jsonify({
            'success': True,
            'stats': stats
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -----------------------------
# Socket.IO Events
# -----------------------------
@socketio.on('connect')
def on_connect():
    """Handle client connection"""
    emit('status', {'connected': True})

@socketio.on('request_availability_update')
def on_request_availability_update():
    """Send current availability to client"""
    try:
        stats = db.get_occupancy_stats()
        
        # Get next available time if all slots are occupied
        next_available_time = None
        if stats['free_slots'] <= 0:
            from datetime import datetime, timedelta
            now = datetime.now()
            next_available = db.find_next_available_time(now, 1)  # 1 hour duration
            if next_available:
                next_available_time = next_available.isoformat()
        
        emit('availability_update', {
            'available': stats['free_slots'] > 0,
            'free_slots': stats['free_slots'],
            'total_slots': stats['total_slots'],
            'occupied_slots': stats['occupied_slots'],
            'occupancy_rate': round(stats['occupancy_rate'], 1),
            'next_available_time': next_available_time,
            'timestamp': datetime.now().isoformat(),
            'slot_predictions': get_slot_predictions(stats)
        })
    except Exception as e:
        emit('error', {'message': str(e)})

# -----------------------------
# Background Tasks
# -----------------------------
def update_availability():
    """Background task to broadcast availability updates"""
    while True:
        try:
            stats = db.get_occupancy_stats()
            predictions = get_slot_predictions(stats)
            socketio.emit('availability_update', {
                'available': stats['free_slots'] > 0,
                'free_slots': stats['free_slots'],
                'total_slots': stats['total_slots'],
                'occupied_slots': stats['occupied_slots'],
                'occupancy_rate': round(stats['occupancy_rate'], 1),
                'timestamp': datetime.now().isoformat(),
                'slot_predictions': predictions
            })
            time.sleep(5)  # Update every 5 seconds
        except Exception as e:
            print(f"Error updating availability: {e}")
            time.sleep(5)

# Start background task
availability_thread = threading.Thread(target=update_availability, daemon=True)
availability_thread.start()

# -----------------------------
# Main Application
# -----------------------------
def print_startup_info():
    """Print startup information"""
    print("=" * 60)
    print("CLIENT PARKING PORTAL")
    print("=" * 60)
    print()
    print("Starting Client Portal...")
    print("Client Portal: http://localhost:5001")
    print("Free Parking View: http://localhost:5001/free-parking")
    print("Client Login: http://localhost:5001/login")
    print("Client Dashboard: http://localhost:5001/dashboard")
    print()
    # Also print best-effort LAN URLs
    try:
        hostname = socket.gethostname()
        lan_ip = socket.gethostbyname(hostname)
        print(f"LAN URL (same Wi‑Fi): http://{lan_ip}:5001")
    except Exception:
        pass
    print("Features:")
    print("- Public parking availability view")
    print("- Client registration and login")
    print("- Real-time availability updates")
    print("- Reservation system with conflict checking")
    print("- Payment processing")
    print()
    print("=" * 60)

def main():
    """Main application entry point"""
    print_startup_info()
    
    try:
        socketio.run(app, host='0.0.0.0', port=5001, debug=False)
    except KeyboardInterrupt:
        print("\n\nShutting down client portal...")
    except Exception as e:
        print(f"\nError starting client portal: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()

import sqlite3
import json
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import os

class DatabaseManager:
    def __init__(self, db_path: str = "parking_system.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the database with all required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Admin users table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Parking slots table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS parking_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_name TEXT UNIQUE NOT NULL,
                coordinates TEXT NOT NULL,
                threshold REAL DEFAULT 0.5,
                std_threshold REAL DEFAULT 0.1,
                edge_threshold REAL DEFAULT 0.3,
                is_active BOOLEAN DEFAULT 1,
                hourly_rate REAL DEFAULT 5.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Reservation requests table (from clients) - New structure
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reservation_requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER,
                name TEXT NOT NULL,
                number_plate TEXT NOT NULL,
                duration INTEGER NOT NULL,
                start_time TIMESTAMP,
                payment_status TEXT DEFAULT 'pending' CHECK (payment_status IN ('pending', 'paid', 'failed', 'refunded')),
                client_data TEXT,  -- JSON string containing full client data
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'rejected', 'expired'))
            )
        ''')
        
        # Active reservations table (accepted requests) - New structure
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                request_id INTEGER NOT NULL,
                slot_id INTEGER,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP NOT NULL,
                duration INTEGER NOT NULL,
                status TEXT DEFAULT 'active' CHECK (status IN ('active', 'completed', 'cancelled')),
                reservation_code TEXT UNIQUE,
                checked_in_at TIMESTAMP,
                checked_out_at TIMESTAMP,
                actual_amount REAL,
                feedback_rating INTEGER,
                feedback_comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (client_id) REFERENCES clients (id),
                FOREIGN KEY (request_id) REFERENCES reservation_requests (request_id)
            )
        ''')
        
        # Payments table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                payment_method TEXT NOT NULL,
                payment_reference TEXT,
                status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'failed', 'refunded')),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP,
                FOREIGN KEY (request_id) REFERENCES reservation_requests (request_id)
            )
        ''')
        
        # System settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_key TEXT UNIQUE NOT NULL,
                setting_value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Clients table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                nrc_number TEXT UNIQUE NOT NULL,
                contact TEXT NOT NULL,
                email TEXT,
                number_plate TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Saved videos table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS saved_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                start_time TIMESTAMP NOT NULL,
                end_time TIMESTAMP NOT NULL,
                duration_seconds INTEGER NOT NULL,
                file_size_bytes INTEGER NOT NULL,
                is_pinned BOOLEAN DEFAULT 0,
                is_deleted BOOLEAN DEFAULT 0,
                deleted_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Backfill columns for existing DBs
        try:
            cursor.execute("ALTER TABLE saved_videos ADD COLUMN is_deleted BOOLEAN DEFAULT 0")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE saved_videos ADD COLUMN deleted_at TIMESTAMP")
        except Exception:
            pass
        
        # Add new fields incrementally; never drop booking tables on startup.
        migrations = {
            'reservation_requests': {
                'start_time': 'TIMESTAMP',
                'client_id': 'INTEGER',
            },
            'reservations': {
                'reservation_code': 'TEXT',
                'checked_in_at': 'TIMESTAMP',
                'checked_out_at': 'TIMESTAMP',
                'actual_amount': 'REAL',
                'feedback_rating': 'INTEGER',
                'feedback_comment': 'TEXT',
            },
            'clients': {
                'password_hash': 'TEXT',
                'vehicle_type': 'TEXT',
                'vehicle_color': 'TEXT',
            },
        }
        for table_name, columns in migrations.items():
            cursor.execute(f'PRAGMA table_info({table_name})')
            existing_columns = {row[1] for row in cursor.fetchall()}
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    cursor.execute(
                        f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}'
                    )
        cursor.execute('SELECT reservation_id FROM reservations WHERE reservation_code IS NULL')
        for (reservation_id,) in cursor.fetchall():
            cursor.execute(
                'UPDATE reservations SET reservation_code = ? WHERE reservation_id = ?',
                (f'SP-{reservation_id:06d}', reservation_id)
            )
        
        conn.commit()
        conn.close()
        
        # Create default admin user
        self.create_default_admin()
        
        # Initialize default settings
        self.init_default_settings()
    
    def create_default_admin(self):
        """Create default admin user if none exists"""
        if not self.get_admin_by_username('admin'):
            password_hash = self.hash_password('admin123')
            self.add_admin('admin', password_hash)
    
    def init_default_settings(self):
        """Initialize default system settings"""
        default_settings = {
            'auto_accept_requests': 'false',
            'fifo_enabled': 'true',
            'max_reservation_hours': '24',
            'default_hourly_rate': '5.0',
            'payment_timeout_minutes': '15'
        }
        
        for key, value in default_settings.items():
            if not self.get_setting(key):
                self.set_setting(key, value)
    
    def hash_password(self, password: str) -> str:
        """Hash password using SHA-256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def add_admin(self, username: str, password_hash: str) -> int:
        """Add new admin user"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO admins (username, password_hash) VALUES (?, ?)',
            (username, password_hash)
        )
        admin_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return admin_id
    
    def get_admin_by_username(self, username: str) -> Optional[Dict]:
        """Get admin user by username"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM admins WHERE username = ?', (username,))
        admin = cursor.fetchone()
        conn.close()
        return dict(admin) if admin else None
    
    def authenticate_admin(self, username: str, password: str) -> bool:
        """Authenticate admin user"""
        admin = self.get_admin_by_username(username)
        if admin:
            return admin['password_hash'] == self.hash_password(password)
        return False
    
    def add_parking_slot(self, slot_data: Dict) -> int:
        """Add new parking slot"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO parking_slots (slot_name, coordinates, threshold, std_threshold, edge_threshold, is_active, hourly_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            slot_data['slot_name'],
            json.dumps(slot_data['coordinates']),
            slot_data.get('threshold', 0.5),
            slot_data.get('std_threshold', 0.1),
            slot_data.get('edge_threshold', 0.3),
            slot_data.get('is_active', 1),
            slot_data.get('hourly_rate', 5.0)
        ))
        slot_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return slot_id
    
    def get_all_parking_slots(self) -> List[Dict]:
        """Get all parking slots"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM parking_slots ORDER BY slot_name')
        slots = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        # Parse coordinates JSON
        for slot in slots:
            slot['coordinates'] = json.loads(slot['coordinates'])
        
        return slots
    
    def get_active_parking_slots(self) -> List[Dict]:
        """Get only active parking slots"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM parking_slots WHERE is_active = 1 ORDER BY slot_name')
        slots = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        # Parse coordinates JSON
        for slot in slots:
            slot['coordinates'] = json.loads(slot['coordinates'])
        
        return slots
    
    def add_reservation_request(self, request_data: Dict) -> int:
        """Add new reservation request from client"""
        print(f"Database adding request with data: {request_data}")
        print(f"Start time being stored: {request_data.get('start_time')}")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Convert client_data to JSON string if it exists
        client_data_json = None
        if 'client_data' in request_data and request_data['client_data']:
            client_data_json = json.dumps(request_data['client_data'])
        
        cursor.execute('''
            INSERT INTO reservation_requests 
            (client_id, name, number_plate, duration, start_time, payment_status, client_data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            request_data.get('client_id'),
            request_data['name'],
            request_data['number_plate'],
            request_data['duration'],
            request_data.get('start_time'),
            request_data.get('payment_status', 'pending'),
            client_data_json
        ))
        request_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        print(f"Database stored request with ID: {request_id}")
        return request_id
    
    def get_pending_requests(self) -> List[Dict]:
        """Get all pending reservation requests"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM reservation_requests
            WHERE status = 'pending'
            ORDER BY created_at ASC
        ''')
        requests = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return requests
    
    def get_all_requests(self) -> List[Dict]:
        """Get all reservation requests"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM reservation_requests 
            ORDER BY created_at DESC
        ''')
        requests = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return requests
    
    def get_request_by_id(self, request_id: int) -> Optional[Dict]:
        """Get a specific request by ID"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM reservation_requests WHERE request_id = ?', (request_id,))
        request = cursor.fetchone()
        conn.close()
        return dict(request) if request else None

    def get_client_requests(self, client_id: int, number_plate: str) -> List[Dict]:
        """Return booking requests belonging to a client, including legacy records."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM reservation_requests
            WHERE client_id = ? OR (client_id IS NULL AND UPPER(number_plate) = UPPER(?))
            ORDER BY created_at DESC
        ''', (client_id, number_plate))
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def accept_request(self, request_id: int, slot_id: int = None, client_data: dict = None) -> int:
        """Accept a reservation request and create reservation + client"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('BEGIN IMMEDIATE')

        # Get request details
        cursor.execute('SELECT * FROM reservation_requests WHERE request_id = ? AND status = \'pending\'', (request_id,))
        request = cursor.fetchone()
        if not request:
            conn.close()
            return None
        
        # Get client data from request or use provided data
        if client_data:
            # Use provided client data (from admin portal)
            client_info = client_data
        elif request['client_data']:
            # Use client data stored in request
            try:
                client_info = json.loads(request['client_data'])
            except (json.JSONDecodeError, TypeError):
                # Fallback to default data if JSON parsing fails
                client_info = {
                    'name': request['name'],
                    'nrc_number': f'CLIENT-{request_id}',  # generate NRC
                    'contact': 'N/A',  # default contact
                    'email': None,
                    'number_plate': request['number_plate']
                }
        else:
            # Fallback to default data (for backward compatibility)
            client_info = {
                'name': request['name'],
                'nrc_number': f'CLIENT-{request_id}',  # generate NRC
                'contact': 'N/A',  # default contact
                'email': None,
                'number_plate': request['number_plate']
            }
        
        # Check if client already exists by number plate
        if request['client_id']:
            cursor.execute('SELECT id FROM clients WHERE id = ?', (request['client_id'],))
        else:
            cursor.execute('SELECT id FROM clients WHERE number_plate = ?', (request['number_plate'],))
        existing_client = cursor.fetchone()
        if existing_client:
            client_id = existing_client['id']
        else:
            cursor.execute('''
                INSERT INTO clients (name, nrc_number, contact, email, number_plate)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                client_info['name'], client_info.get('nrc_number') or f'CLIENT-{request_id}',
                client_info.get('contact') or 'N/A', client_info.get('email'),
                request['number_plate']
            ))
            client_id = cursor.lastrowid
        
        # Determine reservation start time:
        # If the request includes a start_time, use it (allows future/next-day starts);
        # otherwise default to current local time at acceptance.
        req_start_raw = request['start_time']
        try:
            if req_start_raw:
                # Accept both ISO strings and naive local strings
                start_time = datetime.fromisoformat(str(req_start_raw))
            else:
                start_time = datetime.now()
        except Exception:
            start_time = datetime.now()
        # Compute end_time by adding the requested duration in hours
        duration = int(request['duration'])
        end_time = start_time + timedelta(hours=duration)
        
        print(f"Setting reservation start time to: {start_time}")
        print(f"Setting reservation end time to: {end_time}")
        print(f"Duration from request: {duration} hours")
        print(f"Start time ISO format: {start_time.isoformat()}")
        print(f"End time ISO format: {end_time.isoformat()}")
        
        # Allocate a slot that is not already booked during this time window.
        total_slots = self.get_total_calibrated_slots()
        candidate_slots = [slot_id] if slot_id is not None else range(1, total_slots + 1)
        chosen_slot = None
        for candidate in candidate_slots:
            cursor.execute('''
                SELECT 1 FROM reservations
                WHERE slot_id = ? AND status = 'active'
                  AND start_time < ? AND end_time > ?
                LIMIT 1
            ''', (candidate, end_time.isoformat(), start_time.isoformat()))
            if cursor.fetchone() is None:
                chosen_slot = candidate
                break
        if chosen_slot is None:
            conn.rollback()
            conn.close()
            return None
        
        # Create reservation
        cursor.execute('''
            INSERT INTO reservations 
            (client_id, request_id, slot_id, start_time, end_time, duration, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            client_id,
            request_id,
            chosen_slot,
            start_time.isoformat(),
            end_time.isoformat(),
            duration,
            'active'  # explicitly set status
        ))
        reservation_id = cursor.lastrowid
        
        reservation_code = hashlib.sha256(
            f'{request_id}:{reservation_id}:{datetime.now().isoformat()}'.encode()
        ).hexdigest()[:10].upper()
        cursor.execute('UPDATE reservations SET reservation_code = ? WHERE reservation_id = ?',
                       (reservation_code, reservation_id))
        cursor.execute('''
            UPDATE reservation_requests SET status = 'accepted', client_id = ? WHERE request_id = ?
        ''', (client_id, request_id))
        
        conn.commit()
        conn.close()
        return reservation_id


    def update_request_payment_status(self, request_id: int, payment_status: str) -> bool:
        """Update payment status for a reservation request"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reservation_requests
            SET payment_status = ?
            WHERE request_id = ?
        ''', (payment_status, request_id))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def reject_request(self, request_id: int, reason: str = None):
        """Reject a reservation request"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reservation_requests 
            SET status = 'rejected'
            WHERE request_id = ?
        ''', (request_id,))
        conn.commit()
        conn.close()
    
    def get_active_reservations(self) -> List[Dict]:
        """Get all active reservations"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, c.name as client_name, c.number_plate, c.contact, c.email
            FROM reservations r
            LEFT JOIN clients c ON r.client_id = c.id
            WHERE r.status = 'active'
            ORDER BY r.start_time
        ''')
        reservations = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return reservations
    
    def get_all_reservations(self) -> List[Dict]:
        """Get all reservations"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, c.name as client_name, c.number_plate, c.contact, c.email
            FROM reservations r
            LEFT JOIN clients c ON r.client_id = c.id
            ORDER BY r.created_at DESC
        ''')
        reservations = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return reservations

    def get_reservation_by_id(self, reservation_id: int) -> Optional[Dict]:
        """Get a single reservation by ID with client join"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, c.name as client_name, c.number_plate, c.contact, c.email
            FROM reservations r
            LEFT JOIN clients c ON r.client_id = c.id
            WHERE r.reservation_id = ?
        ''', (reservation_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None
    
    def cancel_reservation(self, reservation_id: int):
        """Cancel a reservation"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reservations 
            SET status = 'cancelled'
            WHERE reservation_id = ?
        ''', (reservation_id,))
        conn.commit()
        conn.close()

    def get_client_reservations(self, client_id: int) -> List[Dict]:
        """Return all reservations for one account, newest first."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.*, c.name AS client_name, c.number_plate
            FROM reservations r
            JOIN clients c ON c.id = r.client_id
            WHERE r.client_id = ?
            ORDER BY r.start_time DESC
        ''', (client_id,))
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

    def get_client_reservation(self, reservation_id: int, client_id: int) -> Optional[Dict]:
        """Return a reservation only when it belongs to the given client."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM reservations WHERE reservation_id = ? AND client_id = ?
        ''', (reservation_id, client_id))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def update_client_reservation(self, reservation_id: int, client_id: int,
                                  start_time: str, duration_hours: int) -> bool:
        """Reschedule a future reservation when a non-conflicting slot exists."""
        start = datetime.fromisoformat(start_time)
        end = start + timedelta(hours=duration_hours)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute('''
            SELECT slot_id FROM reservations
            WHERE reservation_id = ? AND client_id = ? AND status = 'active'
                            AND checked_in_at IS NULL AND datetime(start_time) > datetime('now', 'localtime')
        ''', (reservation_id, client_id))
        current = cursor.fetchone()
        if not current:
            conn.rollback()
            conn.close()
            return False

        total_slots = self.get_total_calibrated_slots()
        candidates = list(range(1, total_slots + 1))
        if current[0] in candidates:
            candidates.remove(current[0])
            candidates.insert(0, current[0])
        selected_slot = None
        for candidate in candidates:
            cursor.execute('''
                SELECT 1 FROM reservations
                WHERE slot_id = ? AND status = 'active' AND reservation_id != ?
                  AND start_time < ? AND end_time > ? LIMIT 1
            ''', (candidate, reservation_id, end.isoformat(), start.isoformat()))
            if cursor.fetchone() is None:
                selected_slot = candidate
                break
        if selected_slot is None:
            conn.rollback()
            conn.close()
            return False

        cursor.execute('''
            UPDATE reservations SET slot_id = ?, start_time = ?, end_time = ?, duration = ?
            WHERE reservation_id = ? AND client_id = ?
        ''', (selected_slot, start.isoformat(), end.isoformat(), duration_hours,
              reservation_id, client_id))
        conn.commit()
        conn.close()
        return True

    def cancel_client_reservation(self, reservation_id: int, client_id: int) -> bool:
        """Cancel an owned reservation only before its scheduled start."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reservations SET status = 'cancelled'
            WHERE reservation_id = ? AND client_id = ? AND status = 'active'
                            AND checked_in_at IS NULL AND datetime(start_time) > datetime('now', 'localtime')
        ''', (reservation_id, client_id))
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def check_in_reservation(self, reservation_id: int, client_id: int) -> bool:
        """Check in a client's active reservation once it is within its arrival window."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reservations SET checked_in_at = ?
            WHERE reservation_id = ? AND client_id = ? AND status = 'active'
              AND checked_in_at IS NULL
              AND datetime(start_time) <= datetime('now', 'localtime', '+15 minutes')
              AND datetime(end_time) > datetime('now', 'localtime')
        ''', (datetime.now().isoformat(), reservation_id, client_id))
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def check_out_reservation(self, reservation_id: int, client_id: int) -> Optional[float]:
        """Check out an owned reservation and store its calculated parking charge."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('BEGIN IMMEDIATE')
        cursor.execute('''
            SELECT r.*, COALESCE(s.hourly_rate, ?) AS hourly_rate
            FROM reservations r
            LEFT JOIN parking_slots s ON s.id = r.slot_id
                        WHERE r.reservation_id = ? AND r.client_id = ? AND r.status = 'active'
                            AND r.checked_in_at IS NOT NULL
        ''', (float(self.get_setting('default_hourly_rate') or 5.0), reservation_id, client_id))
        reservation = cursor.fetchone()
        if not reservation:
            conn.rollback()
            conn.close()
            return None

        now = datetime.now()
        try:
            parked_from = datetime.fromisoformat(reservation['checked_in_at']) if reservation['checked_in_at'] else datetime.fromisoformat(reservation['start_time'])
        except (TypeError, ValueError):
            parked_from = now
        elapsed_hours = max(0.0, (now - parked_from).total_seconds() / 3600)
        amount = round(elapsed_hours * float(reservation['hourly_rate']), 2)
        cursor.execute('''
            UPDATE reservations
            SET status = 'completed', checked_out_at = ?, actual_amount = ?
            WHERE reservation_id = ? AND client_id = ? AND status = 'active'
        ''', (now.isoformat(), amount, reservation_id, client_id))
        cursor.execute('''
            INSERT INTO payments (request_id, amount, payment_method, payment_reference, status, processed_at)
            VALUES (?, ?, 'checkout', ?, 'pending', ?)
        ''', (reservation['request_id'], amount, f'reservation-{reservation_id}', now.isoformat()))
        conn.commit()
        conn.close()
        return amount

    def save_reservation_feedback(self, reservation_id: int, client_id: int, rating: int, comment: str) -> bool:
        """Save a one-to-five rating for a completed reservation."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE reservations SET feedback_rating = ?, feedback_comment = ?
            WHERE reservation_id = ? AND client_id = ? AND status = 'completed'
        ''', (rating, comment, reservation_id, client_id))
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def add_payment(self, request_id: int, amount: float, method: str, status: str = 'pending') -> int:
        """Record a payment attempt; this does not represent gateway settlement."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO payments (request_id, amount, payment_method, payment_reference, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (request_id, amount, method, f'local-{datetime.now().timestamp():.0f}', status))
        payment_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return payment_id

    def get_client_payments(self, client_id: int) -> List[Dict]:
        """Return payment records associated with this client's booking requests."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT p.*, q.name, q.number_plate
            FROM payments p
            JOIN reservation_requests q ON q.request_id = p.request_id
            WHERE q.client_id = ? OR (q.client_id IS NULL AND EXISTS (
                SELECT 1 FROM clients c WHERE c.id = ? AND UPPER(c.number_plate) = UPPER(q.number_plate)
            ))
            ORDER BY p.created_at DESC
        ''', (client_id, client_id))
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows
    
    def get_setting(self, key: str) -> Optional[str]:
        """Get system setting value"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT setting_value FROM system_settings WHERE setting_key = ?', (key,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def set_setting(self, key: str, value: str):
        """Set system setting value"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO system_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, value))
        conn.commit()
        conn.close()
    
    def get_occupancy_stats(self) -> Dict:
        """Get parking occupancy statistics including reservations"""
        # Get total slots from calibrated slots (not from database slots)
        total_slots = self.get_total_calibrated_slots()
        active_reservations = len(self.get_active_reservations())
        free_slots = max(0, total_slots - active_reservations)
        
        return {
            'total_slots': total_slots,
            'occupied_slots': active_reservations,
            'free_slots': free_slots,
            'reserved_slots': active_reservations,
            'occupancy_rate': (active_reservations / total_slots * 100) if total_slots > 0 else 0
        }
    
    def get_total_calibrated_slots(self) -> int:
        """Get total number of calibrated slots from the calibration file"""
        try:
            import os
            import json
            config_path = os.path.join(os.path.dirname(__file__), '..', 'python-client', 'config', 'prototype_slots.json')
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    slots = json.load(f)
                if isinstance(slots, list):
                    return max(len(slots), 100)
                return 0
            else:
                # Fallback to demo config
                demo_path = os.path.join(os.path.dirname(__file__), '..', 'python-client', 'config', 'slots_demo.json')
                if os.path.exists(demo_path):
                    with open(demo_path, 'r') as f:
                        slots = json.load(f)
                    if isinstance(slots, list):
                        return max(len(slots), 100)
                    return 0
                return 100  # Default fallback
        except Exception:
            return 1  # Default fallback
    
    def get_next_available_slot_id(self) -> int:
        """Get the next available slot ID (simple sequential assignment)"""
        # For now, return a simple sequential slot ID
        # In a real system, this would check which slots are actually free
        active_reservations = self.get_active_reservations()
        used_slot_ids = [r.get('slot_id') for r in active_reservations if r.get('slot_id')]
        
        # Find first available slot ID
        slot_id = 1
        while slot_id in used_slot_ids:
            slot_id += 1
        
        return slot_id

    def get_next_available_time(self, duration_hours: int = 1) -> Optional[datetime]:
        """Get the next time a slot becomes available for the given duration."""
        return self.find_next_available_time(datetime.now(), duration_hours)
    
    def check_reservation_availability(self, start_time: str, duration_hours: int) -> Dict:
        """Check if a reservation can be made at the specified time"""
        from datetime import datetime, timedelta
        
        requested_start = datetime.fromisoformat(start_time)
        requested_end = requested_start + timedelta(hours=duration_hours)
        
        # Get all active reservations
        reservations = self.get_active_reservations()
        occupied_slots = 0
        
        # Count overlapping reservations
        for reservation in reservations:
            res_start = datetime.fromisoformat(reservation['start_time'])
            res_end = datetime.fromisoformat(reservation['end_time'])
            
            # Check for time overlap
            if requested_start < res_end and requested_end > res_start:
                occupied_slots += 1
        
        total_slots = self.get_total_calibrated_slots()
        available_slots = max(0, total_slots - occupied_slots)
        
        result = {
            'can_reserve': available_slots > 0,
            'available_slots': available_slots,
            'total_slots': total_slots,
            'occupied_slots': occupied_slots,
            'next_available_time': None
        }
        
        # If no slots available, find next available time
        if not result['can_reserve']:
            next_available = self.find_next_available_time(requested_start, duration_hours)
            if next_available:
                result['next_available_time'] = next_available.isoformat()
        
        return result
    
    def find_next_available_time(self, requested_start: datetime, duration_hours: int) -> Optional[datetime]:
        """Find the next available time when a slot becomes free"""
        from datetime import timedelta
        
        reservations = self.get_active_reservations()
        
        if not reservations:
            return requested_start
        
        # Get all time points (start and end times)
        time_points = []
        for reservation in reservations:
            res_start = datetime.fromisoformat(reservation['start_time'])
            res_end = datetime.fromisoformat(reservation['end_time'])
            time_points.append(('start', res_start))
            time_points.append(('end', res_end))
        
        # Sort by time
        time_points.sort(key=lambda x: x[1])
        
        # Check each time point to see if slots become available
        for event_type, time_point in time_points:
            if event_type == 'end':  # A slot just became free
                # Check if we can fit our reservation starting from this time
                test_start = time_point
                test_end = test_start + timedelta(hours=duration_hours)
                
                # Check availability at this time
                availability = self.check_reservation_availability(test_start.isoformat(), duration_hours)
                if availability['can_reserve']:
                    return test_start
        
        return None
    
    def get_reservation_timeline(self) -> List[Dict]:
        """Get a timeline of all reservations for visualization"""
        reservations = self.get_active_reservations()
        timeline = []
        
        for reservation in reservations:
            timeline.append({
                'reservation_id': reservation['reservation_id'],
                'slot_id': reservation['slot_id'],
                'start_time': reservation['start_time'],
                'end_time': reservation['end_time'],
                'duration': reservation['duration'],
                'client_name': reservation.get('client_name', 'Unknown')
            })
        
        return sorted(timeline, key=lambda x: x['start_time'])
    
    def get_slot_utilization_stats(self) -> Dict:
        """Get detailed slot utilization statistics"""
        from datetime import datetime
        
        reservations = self.get_active_reservations()
        now = datetime.now()
        total_slots = self.get_total_calibrated_slots()
        
        stats = {
            'total_slots': total_slots,
            'active_reservations': len(reservations),
            'current_occupied_slots': 0,
            'current_free_slots': total_slots,
            'next_available_time': None,
            'utilization_rate': 0.0
        }
        
        # Count currently occupied slots
        for reservation in reservations:
            res_start = datetime.fromisoformat(reservation['start_time'])
            res_end = datetime.fromisoformat(reservation['end_time'])
            
            if res_start <= now <= res_end:
                stats['current_occupied_slots'] += 1
        
        stats['current_free_slots'] = stats['total_slots'] - stats['current_occupied_slots']
        stats['utilization_rate'] = (stats['current_occupied_slots'] / stats['total_slots']) * 100
        
        # Find next available time
        if stats['current_free_slots'] == 0:
            next_available = self.find_next_available_time(now, 1)
            stats['next_available_time'] = next_available.isoformat() if next_available else None
        
        return stats
    
    def get_reservation_count(self) -> int:
        """Get total number of active reservations"""
        return len(self.get_active_reservations())
    
    def cleanup_expired_reservations(self):
        """Clean up expired reservations and requests while keeping client details"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # First, let's check what reservations exist and their status
        cursor.execute('''
            SELECT reservation_id, start_time, end_time, status, 
                   datetime('now', 'utc') as current_time
            FROM reservations 
            WHERE status = 'active'
        ''')
        active_reservations = cursor.fetchall()
        
        if active_reservations:
            print(f"Found {len(active_reservations)} active reservations:")
            for res in active_reservations:
                print(f"  Reservation {res[0]}: {res[1]} to {res[2]} (Status: {res[3]}, Current: {res[4]})")
        
        # Mark expired reservations as completed (but keep client records)
        # Use UTC time for comparison to match Python datetime
        cursor.execute('''
            UPDATE reservations 
            SET status = 'completed'
            WHERE status = 'active' AND end_time < datetime('now', 'utc')
        ''')
        
        expired_count = cursor.rowcount
        if expired_count > 0:
            print(f"Marked {expired_count} expired reservations as completed")
        else:
            print("No expired reservations found")
        
        # Mark expired requests as expired (requests older than 24 hours)
        cursor.execute('''
            UPDATE reservation_requests 
            SET status = 'expired'
            WHERE status = 'pending' AND created_at < datetime('now', '-24 hours')
        ''')
        
        expired_requests = cursor.rowcount
        if expired_requests > 0:
            print(f"Marked {expired_requests} expired requests as expired")
        
        conn.commit()
        conn.close()
        
        # Note: Client records are intentionally kept for future reference
        return expired_count
    
    def add_saved_video(self, video_data: Dict) -> int:
        """Add saved video metadata to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO saved_videos 
            (filename, file_path, start_time, end_time, duration_seconds, file_size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            video_data['filename'],
            video_data['file_path'],
            video_data['start_time'],
            video_data['end_time'],
            video_data['duration_seconds'],
            video_data['file_size_bytes']
        ))
        video_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return video_id
    
    def get_saved_videos(self, start_date: str = None, end_date: str = None, search_time: str = None) -> List[Dict]:
        """Get saved videos, optionally filtered by date range and time"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        print(f"DB: Getting videos with start_date={start_date}, end_date={end_date}, search_time={search_time}")
        
        if start_date and end_date:
            if search_time:
                # Search by both date and time
                cursor.execute('''
                    SELECT * FROM saved_videos 
                    WHERE is_deleted = 0 AND date(start_time) = ? AND time(start_time) LIKE ?
                    ORDER BY start_time DESC
                ''', (start_date, f'{search_time}%'))
            else:
                # Search by date only
                cursor.execute('''
                    SELECT * FROM saved_videos 
                    WHERE is_deleted = 0 AND date(start_time) = ?
                    ORDER BY start_time DESC
                ''', (start_date,))
        elif search_time:
            # Search by time only (any date)
            cursor.execute('''
                SELECT * FROM saved_videos 
                WHERE is_deleted = 0 AND time(start_time) LIKE ?
                ORDER BY start_time DESC
            ''', (f'{search_time}%',))
        else:
            cursor.execute('SELECT * FROM saved_videos WHERE is_deleted = 0 ORDER BY start_time DESC')
        
        videos = [dict(row) for row in cursor.fetchall()]
        print(f"DB: Found {len(videos)} videos")
        conn.close()
        return videos
    
    def get_saved_video_by_id(self, video_id: int) -> Optional[Dict]:
        """Get saved video by ID"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM saved_videos WHERE id = ?', (video_id,))
        video = cursor.fetchone()
        conn.close()
        return dict(video) if video else None
    
    def delete_saved_video(self, video_id: int) -> bool:
        """Soft-delete saved video"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE saved_videos SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, is_pinned = 0 WHERE id = ?', (video_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    def delete_all_saved_videos(self) -> int:
        """Soft-delete all saved videos"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM saved_videos WHERE is_deleted = 0')
        count = cursor.fetchone()[0]
        cursor.execute('UPDATE saved_videos SET is_deleted = 1, deleted_at = CURRENT_TIMESTAMP, is_pinned = 0 WHERE is_deleted = 0')
        conn.commit()
        conn.close()
        return count
    
    def get_pinned_videos(self) -> List[Dict]:
        """Get all pinned videos"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM saved_videos WHERE is_deleted = 0 AND is_pinned = 1 ORDER BY created_at DESC')
        videos = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return videos
    
    def pin_video(self, video_id: int) -> bool:
        """Pin a video as important (only if not already pinned)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Check if video is already pinned
        cursor.execute('SELECT is_pinned FROM saved_videos WHERE id = ?', (video_id,))
        result = cursor.fetchone()
        
        if not result:
            conn.close()
            return False  # Video not found
        
        if result[0] == 1:
            conn.close()
            return False  # Already pinned
        
        # Pin the video
        cursor.execute('UPDATE saved_videos SET is_pinned = 1 WHERE id = ?', (video_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success
    
    def unpin_video(self, video_id: int) -> bool:
        """Unpin a video"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE saved_videos SET is_pinned = 0 WHERE id = ?', (video_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def get_deleted_videos(self, start_date: str = None, end_date: str = None, search_time: str = None) -> List[Dict]:
        """Get soft-deleted videos (trash), with optional filters on original start_time"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if start_date and end_date:
            if search_time:
                cursor.execute('''
                    SELECT * FROM saved_videos
                    WHERE is_deleted = 1 AND date(start_time) = ? AND time(start_time) LIKE ?
                    ORDER BY deleted_at DESC
                ''', (start_date, f'{search_time}%'))
            else:
                cursor.execute('''
                    SELECT * FROM saved_videos
                    WHERE is_deleted = 1 AND date(start_time) = ?
                    ORDER BY deleted_at DESC
                ''', (start_date,))
        elif search_time:
            cursor.execute('''
                SELECT * FROM saved_videos
                WHERE is_deleted = 1 AND time(start_time) LIKE ?
                ORDER BY deleted_at DESC
            ''', (f'{search_time}%',))
        else:
            cursor.execute('SELECT * FROM saved_videos WHERE is_deleted = 1 ORDER BY deleted_at DESC')

        videos = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return videos

    def recover_video(self, video_id: int) -> bool:
        """Recover a soft-deleted video"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('UPDATE saved_videos SET is_deleted = 0, deleted_at = NULL WHERE id = ?', (video_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def delete_permanently(self, video_id: int) -> bool:
        """Permanently remove a soft-deleted video record"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM saved_videos WHERE id = ? AND is_deleted = 1', (video_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def purge_deleted_older_than_days(self, days: int) -> List[int]:
        """Return IDs of videos to purge and delete them from DB after caller cleans files"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id FROM saved_videos 
            WHERE is_deleted = 1 AND deleted_at IS NOT NULL AND deleted_at <= datetime('now', ?)
        ''', (f'-{days} days',))
        ids = [row['id'] for row in cursor.fetchall()]
        if ids:
            cursor.executemany('DELETE FROM saved_videos WHERE id = ?', [(i,) for i in ids])
            conn.commit()
        conn.close()
        return ids

    # Client management methods
    def add_client(self, client_data: Dict) -> int:
        """Add new client"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO clients
                (name, nrc_number, contact, email, number_plate, password_hash, vehicle_type, vehicle_color)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            client_data['name'],
            client_data.get('nrc_number'),
            client_data['contact'],
            client_data.get('email'),
            client_data['number_plate'],
            client_data.get('password_hash'),
            client_data.get('vehicle_type'),
            client_data.get('vehicle_color'),
        ))
        client_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return client_id

    def get_all_clients(self) -> List[Dict]:
        """Get all clients"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients ORDER BY id ASC')
        clients = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return clients

    def get_client_by_id(self, client_id: int) -> Optional[Dict]:
        """Get client by ID"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients WHERE id = ?', (client_id,))
        client = cursor.fetchone()
        conn.close()
        return dict(client) if client else None

    def get_client_by_nrc(self, nrc_number: str) -> Optional[Dict]:
        """Get client by NRC number"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients WHERE nrc_number = ?', (nrc_number,))
        client = cursor.fetchone()
        conn.close()
        return dict(client) if client else None

    def get_client_by_plate(self, number_plate: str) -> Optional[Dict]:
        """Get client by number plate"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM clients WHERE number_plate = ?', (number_plate,))
        client = cursor.fetchone()
        conn.close()
        return dict(client) if client else None

    def update_client(self, client_id: int, client_data: Dict) -> bool:
        """Update client information"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE clients 
            SET name = ?, nrc_number = ?, contact = ?, email = ?, number_plate = ?,
                vehicle_type = ?, vehicle_color = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (
            client_data['name'],
            client_data.get('nrc_number'),
            client_data['contact'],
            client_data.get('email'),
            client_data['number_plate'],
            client_data.get('vehicle_type'),
            client_data.get('vehicle_color'),
            client_id
        ))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def update_client_password(self, client_id: int, password_hash: str) -> bool:
        """Store a password hash for a client account."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE clients SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
        ''', (password_hash, client_id))
        changed = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return changed

    def delete_client(self, client_id: int) -> bool:
        """Delete client"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM clients WHERE id = ?', (client_id,))
        success = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return success

    def search_clients(self, search_term: str) -> List[Dict]:
        """Search clients by name, NRC, contact, email, or number plate"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        search_pattern = f'%{search_term}%'
        cursor.execute('''
            SELECT * FROM clients 
            WHERE name LIKE ? OR nrc_number LIKE ? OR contact LIKE ? OR email LIKE ? OR number_plate LIKE ?
            ORDER BY created_at DESC
        ''', (search_pattern, search_pattern, search_pattern, search_pattern, search_pattern))
        clients = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return clients

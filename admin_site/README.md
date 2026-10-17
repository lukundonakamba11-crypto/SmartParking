# Enhanced Parking Management System

A comprehensive Flask-based parking management system with ESP32-CAM integration, real-time monitoring, and automated reservation processing.

##  Features

### Admin Dashboard
- **Authentication**: Secure admin login system
- **Real-time Monitoring**: Live parking slot occupancy
- **Side Navigation**: Easy access to all system functions
- **Controls**: Direct access to ESP32-CAM management tools
- **Reservation Management**: Accept/reject/cancel reservations
- **Request Processing**: Manual and automatic FIFO processing
- **Slot Visualization**: Visual representation of parking slots

### Client Portal
- **Availability Check**: Real-time parking availability
- **Reservation Requests**: Easy booking interface
- **Cost Calculation**: Automatic pricing based on duration
- **Status Tracking**: Monitor reservation status

### System Integration
- **ESP32-CAM**: Live stream and detection integration
- **Python Scripts**: Calibration, detection, and streaming
- **Database**: SQLite with comprehensive data management
- **Real-time Updates**: Socket.IO for live data synchronization
- **Background Tasks**: Automatic cleanup and processing

##  Project Structure

```
car-park-web-app/
├── app.py                 # Main Flask application
├── database.py            # Database management
├── run_app.py            # Application launcher
├── templates/
│   ├── login.html        # Admin login page
│   ├── dashboard.html    # Main admin dashboard
│   ├── controls.html     # System controls interface
│   └── client.html       # Client reservation portal
├── static/
│   ├── css/
│   │   └── style.css     # Custom styles
│   └── js/
│       └── main.js       # JavaScript functionality
└── README.md             # This file
```

## Installation & Setup

### Prerequisites
- Python 3.8+
- ESP32-CAM connected and configured
- Node.js server running (for ESP32-CAM bridge)

### Installation Steps

1. **Install Python Dependencies**
   ```bash
   cd car-park-web-app
   pip install flask flask-socketio requests opencv-python numpy
   ```

2. **Start the Application**
   ```bash
   python run_app.py
   ```

3. **Access the System**
   - Admin Dashboard: http://localhost:5000
   - Client Portal: http://localhost:5000/client
   - Controls: http://localhost:5000/controls

## Default Login

- **Username**: `admin`
- **Password**: `admin123`

## System Components

### 1. Admin Dashboard (`/dashboard`)
- **Overview**: Real-time statistics and occupancy
- **Controls**: Access to ESP32-CAM management
- **Parking Slots**: Visual slot status with hover effects
- **Reservations**: Active reservation management
- **Requests**: Pending request processing

### 2. System Controls (`/controls`)
- **Live Stream**: Start/stop ESP32-CAM streaming
- **Calibration**: Slot calibration tools
- **Detection**: Parking detection system
- **Viewer**: Stream viewing interface
- **Logs**: Real-time system logs

### 3. Client Portal (`/client`)
- **Availability**: Check parking availability
- **Reservation**: Submit booking requests
- **Pricing**: Automatic cost calculation
- **Status**: Track reservation status

## Real-time Features

### Socket.IO Events
- `occupancy_update`: Live slot occupancy updates
- `request_processed`: FIFO processing notifications
- `live_frame`: ESP32-CAM stream frames
- `calibration_status`: Calibration process updates

### Background Tasks
- **Cleanup**: Automatic expired reservation cleanup
- **FIFO Processing**: Automatic request processing
- **Status Updates**: Real-time occupancy monitoring

##  Database Schema

### Tables
- `admins`: Admin user accounts
- `parking_slots`: Parking slot definitions
- `reservation_requests`: Client booking requests
- `reservations`: Active reservations
- `payments`: Payment tracking
- `system_settings`: Configuration settings

##  API Endpoints

### Admin API (Authentication Required)
- `GET /api/occupancy` - Get occupancy statistics
- `GET /api/slots` - Get all parking slots
- `GET /api/requests` - Get all requests
- `GET /api/reservations` - Get all reservations
- `POST /api/requests/{id}/accept` - Accept request
- `POST /api/requests/{id}/reject` - Reject request
- `POST /api/reservations/{id}/cancel` - Cancel reservation

### Client API (Public)
- `GET /api/client/availability` - Check availability
- `POST /api/client/reserve` - Submit reservation
- `GET /api/client/request/{id}/status` - Check status

##  Usage Workflow

### Admin Workflow
1. **Login** to admin dashboard
2. **Monitor** real-time occupancy
3. **Process** reservation requests (manual or automatic)
4. **Manage** active reservations
5. **Control** ESP32-CAM systems

### Client Workflow
1. **Check** parking availability
2. **Submit** reservation request
3. **Wait** for admin approval
4. **Track** reservation status
5. **Arrive** at reserved time

### System Workflow
1. **ESP32-CAM** provides live stream
2. **Detection** identifies occupied slots
3. **Database** tracks reservations
4. **FIFO** processes requests automatically
5. **Real-time** updates all interfaces

## Configuration

### System Settings
- `auto_accept_requests`: Enable/disable auto-acceptance
- `fifo_enabled`: Enable/disable FIFO processing
- `max_reservation_hours`: Maximum booking duration
- `default_hourly_rate`: Default parking rate
- `payment_timeout_minutes`: Payment timeout

### ESP32-CAM Integration
- **Stream URL**: Configured in `NODE_SERVER_URL`
- **Detection**: Integrated with `prototype_client.py`
- **Calibration**: Uses `calibrate_slots.py`

##  Troubleshooting

### Common Issues
1. **Database Errors**: Check SQLite file permissions
2. **ESP32-CAM Connection**: Verify Node.js server is running
3. **Python Scripts**: Ensure all dependencies are installed
4. **Socket.IO Issues**: Check browser console for errors

### Debug Mode
Enable debug logging by setting environment variable:
```bash
export FLASK_DEBUG=1
python run_app.py
```

##  Future Enhancements

- **Payment Gateway**: Integration with payment processors
- **Mobile App**: Native mobile application
- **Analytics**: Detailed usage statistics
- **Notifications**: SMS/Email alerts
- **Multi-location**: Support for multiple parking lots
- **AI Integration**: Advanced occupancy prediction

##  Support

For technical support or feature requests, please refer to the main project documentation or contact the development team.

---

**Version**: 2.0  
**Last Updated**: 2024  
**Compatibility**: Python 3.8+, Flask 2.0+, ESP32-CAM

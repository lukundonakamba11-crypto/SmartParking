# Client Parking Portal

A modern, responsive web application for clients to view parking availability, register accounts, and make parking reservations.

## Features

### Public Features (No Login Required)
- **Free Parking View**: Real-time parking availability display
- **Live Updates**: Automatic updates every 5 seconds
- **Responsive Design**: Works on desktop, tablet, and mobile devices
- **Registration**: Easy account creation process

### Client Features (Login Required)
- **Dashboard**: Personalized parking management interface
- **Reservation System**: Submit, view, reschedule, and cancel reservations
- **Conflict Detection**: Slot assignment is rechecked transactionally to prevent overlapping bookings
- **Account Profile**: Update contact and vehicle information and change your password
- **Check-in / Check-out**: Check in with a reservation code, end an active session, and calculate a time-based amount due
- **History and Feedback**: Review requests, reservations, and payment records; rate completed visits
- **Password Recovery**: Signed, one-hour reset links sent by email when SMTP is configured
- **Payment Status**: Cash is recorded as awaiting staff verification; card/mobile-money capture is disabled until a payment provider is integrated

### User Experience
- **Modern UI**: Clean, professional design with gradients and animations
- **Real-time Updates**: Live parking status via WebSocket connections
- **Mobile Responsive**: Optimized for all screen sizes
- **Accessibility**: WCAG compliant with keyboard navigation support

## Quick Start

### Prerequisites
- Python 3.7+
- Flask
- Flask-SocketIO
- SQLite3 (included with Python)

### Installation

1. **Navigate to the client-portal directory:**
   ```bash
   cd client-portal
   ```

2. **Install dependencies:**
   ```bash
   pip install flask flask-socketio
   ```

3. **Start the client portal:**
   ```bash
   python app.py
   ```

4. **Access the portal:**
   - Open your browser to: http://localhost:5001
   - Free parking view: http://localhost:5001/free-parking
   - Client login: http://localhost:5001/login

### Alternative Startup Methods

**Windows:**
```bash
start_client_portal.bat
```

**PowerShell:**
```powershell
.\start_client_portal.ps1
```

**From project root:**
```bash
python start_client_portal.py
```

## Usage Guide

### For New Users

1. **View Parking Availability**
   - Visit the free parking page to see real-time availability
   - No account required for viewing

2. **Create Account**
   - Click "Create Account" or "Register"
   - Fill in your details:
     - Full Name
     - NRC Number
     - Contact Number
     - Email (optional)
       - Vehicle Number Plate and optional type/color
       - A password (at least 8 characters)

3. **Login**
   - Use your vehicle plate as the username and your chosen password
   - Access your personalized dashboard

### For Returning Users

1. **Login**
   - Enter your number plate and chosen password
   - Access your dashboard

2. **Make Reservations**
   - Click "Reserve Parking Spot"
   - Fill in reservation details:
     - Start time (must be future time)
     - Duration (1-24 hours)
   - Proceed to payment

3. **Manage parking**
   - View accepted reservation details and reservation code in Dashboard
   - Enter that code to check in within 15 minutes before the scheduled start
   - Check out to release the space and calculate the elapsed-time fee
   - Submit a rating after checkout

4. **Payment**
   - Cash payment can be recorded as pending staff verification
   - Card and mobile-money payments require a configured provider and are not charged by this demo

## API Endpoints

### Public Endpoints
- `GET /api/availability` - Get parking availability (no auth required)

### Client Endpoints (Login Required)
- `GET /api/client/availability` - Get parking availability for clients
- `POST /api/client/reserve` - Create reservation request
- `GET /api/client/request/<id>/status` - Get reservation status
- `POST /api/client/payment` - Process payment

## Database Integration

The client portal integrates with the main parking system database:
- **Clients Table**: User account information
- **Reservation Requests**: Pending reservations
- **Reservations**: Confirmed reservations
- **Parking Slots**: Available parking spaces

Database initialization is additive and preserves existing reservation/request tables. Keep a backup before deploying schema upgrades.

## Configuration and integration limits

- Set `CLIENT_PORTAL_SECRET_KEY` to a private random value before deployment.
- Password reset delivery requires `PARKING_SMTP_HOST`, `PARKING_SMTP_FROM`, and optionally `PARKING_SMTP_PORT`, `PARKING_SMTP_USER`, and `PARKING_SMTP_PASSWORD`. Without SMTP, reset links are not delivered.
- Card/mobile-money checkout requires a real payment gateway and webhook verification; this project does not capture online payments.
- The workspace has no geographic lot coordinates, opening-hour/facility/type metadata, or lot-distance data. It cannot truthfully render a geographic lot map or distance/lot filters yet; existing slot coordinates are camera-image calibration points, not GPS.
- Camera detection is not currently persisted as authoritative per-slot occupancy, so availability is reservation-derived rather than guaranteed live sensor occupancy. Arrival detection needs a sensor-to-reservation integration.
- Navigation to a lot or an internal bay requires configured map coordinates and indoor-routing data.

## Real-time Features

### WebSocket Events
- `availability_update` - Live parking status updates
- `request_availability_update` - Request current status
- `connect` - Client connection status

### Update Frequency
- **Availability Updates**: Every 5 seconds
- **Client Requests**: On-demand
- **Reservation Status**: Real-time

## Security Features

- **Session Management**: Secure client sessions
- **Input Validation**: Server-side validation for all forms
- **SQL Injection Protection**: Parameterized queries
- **XSS Protection**: Template escaping
- **CSRF Protection**: Flask-WTF integration ready

## Responsive Design

### Breakpoints
- **Mobile**: < 576px
- **Tablet**: 576px - 768px
- **Desktop**: > 768px

### Features
- **Flexible Grid**: Bootstrap 5 grid system
- **Touch Friendly**: Large buttons and touch targets
- **Readable Text**: Optimized font sizes
- **Fast Loading**: Optimized assets and lazy loading

## Browser Support

- **Chrome**: 90+
- **Firefox**: 88+
- **Safari**: 14+
- **Edge**: 90+
- **Mobile Browsers**: iOS Safari, Chrome Mobile

## Development

### Project Structure
```
client-portal/
├── app.py                 # Main Flask application
├── templates/            # HTML templates
│   ├── base.html         # Base template
│   ├── free_parking.html # Public parking view
│   ├── client_login.html # Login page
│   ├── client_register.html # Registration page
│   └── client_dashboard.html # Client dashboard
├── static/               # Static assets
│   └── css/
│       └── style.css     # Custom styles
└── README.md            # This file
```

### Customization

**Styling:**
- Edit `static/css/style.css` for custom styles
- CSS variables for easy color scheme changes
- Responsive design utilities included

**Templates:**
- Jinja2 templates with Bootstrap 5
- Modular design for easy customization
- Flash message support

**Functionality:**
- Modular route structure
- Easy to add new features
- Database integration ready

## Troubleshooting

### Common Issues

1. **Port Already in Use**
   - Change port in `app.py` (line with `port=5001`)
   - Or stop other services using port 5001

2. **Database Connection Error**
   - Ensure the main parking system database exists
   - Check database path in `app.py`

3. **Socket.IO Connection Issues**
   - Check firewall settings
   - Ensure WebSocket support in browser

4. **Styling Issues**
   - Clear browser cache
   - Check CSS file path
   - Verify Bootstrap CDN connection

### Debug Mode

Enable debug mode in `app.py`:
```python
socketio.run(app, host='0.0.0.0', port=5001, debug=True)
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is part of the Smart Parking System and follows the same license terms.

## Support

For support and questions:
- Check the troubleshooting section
- Review the main system documentation
- Contact the development team

---

**Note**: This client portal is designed to work alongside the main parking management system. Ensure the main system is running and the database is properly configured before using the client portal.

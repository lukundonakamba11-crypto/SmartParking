# Video Recording System

This document describes the video recording functionality added to the ESP32-CAM parking management system.

## Features

###  Automatic Video Recording
- **24-hour segments**: Videos are automatically recorded in daily segments
- **Auto-start**: Recording starts automatically at the beginning of each day
- **Auto-stop**: Recording stops at the end of each day (23:59)
- **Continuous operation**: System runs in the background without manual intervention

###  Manual Recording Control
- **Start/Stop buttons**: Manual control via web interface
- **Real-time status**: Live recording status and duration display
- **Socket.IO integration**: Real-time updates via WebSocket

###  Video Management
- **Database storage**: Video metadata stored in SQLite database
- **File management**: Videos saved to `saved_videos/` directory
- **Date filtering**: Filter videos by date range
- **Download**: Download individual video files
- **Delete**: Delete individual videos or all videos at once

###  Video Information
- **Filename**: Timestamped filenames (e.g., `parking_recording_20241201_143022.mp4`)
- **Duration**: Recording duration in minutes
- **File size**: File size in MB
- **Start/End times**: Precise recording timestamps
- **Status**: File availability status

## Technical Details

### Database Schema
```sql
CREATE TABLE saved_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    start_time TIMESTAMP NOT NULL,
    end_time TIMESTAMP NOT NULL,
    duration_seconds INTEGER NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Video Specifications
- **Format**: MP4 (H.264 codec)
- **Resolution**: 640x480 pixels
- **Frame rate**: 10 FPS
- **Codec**: MP4V (compatible with most players)
- **Max duration**: 24 hours per file

### File Structure
```
car-park-web-app/
├── saved_videos/           # Video files directory
│   ├── parking_recording_20241201_000000.mp4
│   ├── parking_recording_20241202_000000.mp4
│   └── ...
├── templates/
│   └── saved_videos.html   # Video management interface
└── app.py                  # Main Flask application
```

## API Endpoints

### Video Management
- `GET /api/videos` - Get all videos (with optional date filtering)
- `GET /api/videos/<id>` - Get specific video details
- `GET /api/videos/<id>/download` - Download video file
- `DELETE /api/videos/<id>` - Delete specific video
- `DELETE /api/videos/delete_all` - Delete all videos

### Recording Control
- `POST /api/videos/recording/start` - Start recording
- `POST /api/videos/recording/stop` - Stop recording
- `GET /api/videos/recording/status` - Get recording status

### Socket.IO Events
- `start_video_recording` - Start recording via WebSocket
- `stop_video_recording` - Stop recording via WebSocket
- `get_video_recording_status` - Get status via WebSocket
- `video_recording_status` - Status updates (emitted)
- `video_recording_error` - Error messages (emitted)

## Usage

### Web Interface
1. **Access**: Navigate to `/saved_videos` in the admin dashboard
2. **Manual Recording**: Use Start/Stop buttons for manual control
3. **Filter Videos**: Use date range filters to find specific videos
4. **Download**: Click download button to save videos locally
5. **Delete**: Use delete buttons to remove videos

### Automatic Recording
- Recording starts automatically at 00:00 each day
- Recording stops automatically at 23:59 each day
- No manual intervention required
- Background process handles all operations

### File Management
- Videos are automatically saved with timestamped filenames
- Metadata is stored in the database
- Files are organized in the `saved_videos/` directory
- Automatic cleanup can be configured

## Configuration

### Video Settings
```python
# In app.py VideoRecorder class
fps = 10                    # Frame rate
frame_size = (640, 480)     # Resolution
max_frames = 864000         # Max frames per day (24 hours at 10 FPS)
```

### Directory Settings
```python
# Video output directory
output_dir = "saved_videos"
```

## Monitoring

### Real-time Status
- Recording status displayed in web interface
- Live duration counter during recording
- File count and total size statistics
- Error messages and alerts

### Background Tasks
- Auto-recording runs in separate thread
- Database cleanup runs every minute
- Status updates every 5 seconds
- Error handling and recovery

## Troubleshooting

### Common Issues

1. **Recording not starting**
   - Check ESP32-CAM connection
   - Verify Node.js server is running
   - Check disk space availability

2. **Videos not saving**
   - Check `saved_videos/` directory permissions
   - Verify database connection
   - Check for disk space issues

3. **Poor video quality**
   - Adjust frame rate in VideoRecorder class
   - Check ESP32-CAM resolution settings
   - Verify network connection stability

4. **Database errors**
   - Check database file permissions
   - Verify SQLite installation
   - Check for disk space

### Testing
Run the test script to verify functionality:
```bash
cd car-park-web-app
python test_video_recording.py
```

## Security Considerations

- Video files are stored locally (not uploaded to cloud)
- Access requires admin authentication
- Files are not publicly accessible via web
- Database contains only metadata (not video content)

## Performance Notes

- Recording uses minimal CPU resources
- Videos are compressed to save disk space
- Background tasks are optimized for efficiency
- Database queries are indexed for speed

## Future Enhancements

- Cloud storage integration
- Video compression optimization
- Motion detection triggers
- Email alerts for storage issues
- Video thumbnail generation
- Advanced filtering options

(function () {
    const socket = io(window.SOCKET_URL, { transports: ['websocket'] });

    const liveImg = document.getElementById('liveFrame');
    const logsEl = document.getElementById('logs');

    function log(line) {
        if (!logsEl) return;
        logsEl.textContent += line + "\n";
        logsEl.scrollTop = logsEl.scrollHeight;
    }

    socket.on('connect', () => {
        log('[socket] connected');
    });
    socket.on('disconnect', () => {
        log('[socket] disconnected');
    });

    socket.on('status', (s) => {
        log('[status] ' + JSON.stringify(s));
    });

    socket.on('live_frame', (payload) => {
        if (liveImg && payload && payload.frame) {
            liveImg.src = payload.frame;
        }
    });

    socket.on('script_log', (m) => {
        log(`[${m.script}][${m.level}] ${m.line}`);
    });

    // Status event handlers
    socket.on('live_status', (status) => {
        log(`[live] ${status.running ? 'Started' : 'Stopped'}`);
    });

    socket.on('calibration_status', (status) => {
        log(`[calibration] ${status.running ? 'Started' : 'Stopped'}`);
    });

    socket.on('detection_status', (status) => {
        log(`[detection] ${status.running ? 'Started' : 'Stopped'}`);
    });

    socket.on('viewer_status', (status) => {
        log(`[viewer] ${status.running ? 'Started' : 'Stopped'}`);
    });

    const byId = (id) => document.getElementById(id);
    const bind = (id, evt, fn) => { const el = byId(id); if (el) el.addEventListener(evt, fn); };

    bind('btnStartLive', 'click', () => socket.emit('start_live'));
    bind('btnStopLive', 'click', () => socket.emit('stop_live'));
    bind('btnStartViewer', 'click', () => socket.emit('start_viewer'));
    bind('btnStopViewer', 'click', () => socket.emit('stop_viewer'));
    bind('btnStartCalibration', 'click', () => socket.emit('start_calibration'));
    bind('btnStopCalibration', 'click', () => socket.emit('stop_calibration'));
    bind('btnStartDetection', 'click', () => socket.emit('start_detection'));
    bind('btnStopDetection', 'click', () => socket.emit('stop_detection'));
})();



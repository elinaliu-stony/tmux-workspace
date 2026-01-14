import os
import pty
import select
import subprocess
import struct
import fcntl
import termios
import signal
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import database

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tmux-workspace-secret'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')

# Track active PTY sessions: {sid: {'fd': fd, 'pid': pid, 'type': 'tmux'|'bash'}}
terminals = {}

# --- HTTP Routes ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/sessions')
def get_sessions():
    """List all tmux sessions."""
    try:
        result = subprocess.run(
            ['tmux', 'list-sessions', '-F', '#{session_name}:#{session_windows}:#{session_attached}'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify([])

        sessions = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(':')
            if len(parts) >= 3:
                sessions.append({
                    'name': parts[0],
                    'windows': int(parts[1]),
                    'attached': parts[2] == '1'
                })
        return jsonify(sessions)
    except Exception as e:
        print(f"Error listing sessions: {e}")
        return jsonify([])

@app.route('/api/sessions/<session>/windows')
def get_windows(session):
    """List windows in a tmux session."""
    try:
        result = subprocess.run(
            ['tmux', 'list-windows', '-t', session, '-F', '#{window_index}:#{window_name}'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return jsonify([])

        windows = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(':', 1)
            if len(parts) >= 2:
                windows.append({
                    'index': int(parts[0]),
                    'name': parts[1]
                })
        return jsonify(windows)
    except Exception as e:
        print(f"Error listing windows: {e}")
        return jsonify([])

@app.route('/api/layout', methods=['GET'])
def get_layout():
    """Get saved layout from database."""
    layout = database.get_layout()
    return jsonify(layout or {})

@app.route('/api/layout', methods=['POST'])
def save_layout():
    """Save layout to database."""
    data = request.get_json()
    if data:
        database.save_layout(data)
    return jsonify({'status': 'ok'})

@app.route('/api/layout', methods=['DELETE'])
def delete_layout():
    """Delete saved layout."""
    database.delete_layout()
    return jsonify({'status': 'ok'})

# --- Socket.IO Events ---

@socketio.on('connect')
def on_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    print(f"Client disconnected: {sid}")
    cleanup_terminal(sid)

@socketio.on('open_terminal')
def on_open_terminal(data):
    """Open a new terminal (tmux session or bash)."""
    sid = request.sid
    terminal_type = data.get('type', 'bash')
    session = data.get('session')
    window = data.get('window', 0)

    # Clean up existing terminal for this session
    cleanup_terminal(sid)

    try:
        # Create PTY
        pid, fd = pty.fork()

        if pid == 0:
            # Child process
            if terminal_type == 'tmux' and session:
                os.execlp('tmux', 'tmux', 'attach-session', '-t', f'{session}:{window}')
            else:
                # Spawn bash
                shell = os.environ.get('SHELL', '/bin/bash')
                os.execlp(shell, shell)
        else:
            # Parent process
            # Set non-blocking
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            terminals[sid] = {
                'fd': fd,
                'pid': pid,
                'type': terminal_type,
                'session': session,
                'window': window
            }

            # Start reading from PTY
            socketio.start_background_task(read_pty, sid, fd)
            emit('terminal_ready', {'status': 'ok'})

    except Exception as e:
        print(f"Error opening terminal: {e}")
        emit('terminal_error', {'error': str(e)})

@socketio.on('terminal_input')
def on_terminal_input(data):
    """Send input to terminal."""
    sid = request.sid
    if sid not in terminals:
        return

    fd = terminals[sid]['fd']
    try:
        os.write(fd, data.encode('utf-8'))
    except Exception as e:
        print(f"Error writing to PTY: {e}")

@socketio.on('terminal_resize')
def on_terminal_resize(data):
    """Resize terminal."""
    sid = request.sid
    if sid not in terminals:
        return

    fd = terminals[sid]['fd']
    rows = data.get('rows', 24)
    cols = data.get('cols', 80)

    try:
        winsize = struct.pack('HHHH', rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except Exception as e:
        print(f"Error resizing PTY: {e}")

def read_pty(sid, fd):
    """Background task to read from PTY and send to client."""
    while sid in terminals and terminals[sid]['fd'] == fd:
        try:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(fd, 4096)
                    if data:
                        socketio.emit('terminal_output', data.decode('utf-8', errors='replace'), to=sid)
                    else:
                        # EOF
                        break
                except OSError:
                    break
        except Exception as e:
            print(f"Error reading from PTY: {e}")
            break

    # Terminal closed
    socketio.emit('terminal_closed', {}, to=sid)
    cleanup_terminal(sid)

def cleanup_terminal(sid):
    """Clean up terminal resources."""
    if sid not in terminals:
        return

    terminal = terminals.pop(sid, None)
    if terminal:
        try:
            os.close(terminal['fd'])
        except:
            pass
        try:
            os.kill(terminal['pid'], signal.SIGTERM)
        except:
            pass

if __name__ == '__main__':
    print("Starting Tmux Workspace on http://localhost:5001")
    socketio.run(app, host='0.0.0.0', port=5001, debug=True)

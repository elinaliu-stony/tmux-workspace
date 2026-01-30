import os
import sys
import pty
import select
import subprocess
import struct
import fcntl
import termios
import signal
import threading
import secrets
import logging
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, make_response
from flask_socketio import SocketIO, emit, disconnect
from functools import wraps
from . import database

# --- Token Authentication ---
TOKEN_ENABLED = True  # Will be set by --no-token flag

def check_token():
    """Check if request has valid token via query param or cookie."""
    if not TOKEN_ENABLED:
        return True
    token = request.args.get('token') or request.cookies.get('tmux_token')
    return token == ACCESS_TOKEN

def token_required(f):
    """Decorator for routes that require token auth."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_token():
            if request.is_json or request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized', 'message': 'Valid token required'}), 401
            return redirect(f'/login?next={request.path}')
        return f(*args, **kwargs)
    return decorated

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f'tmux-workspace-{datetime.now().strftime("%Y%m%d")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# --- Access Token ---
ACCESS_TOKEN = secrets.token_urlsafe(24)

# --- Shutdown Handling ---
shutdown_requested = False
shutdown_confirmed = False
shutdown_timer = None

def handle_shutdown(signum, frame):
    global shutdown_requested, shutdown_confirmed, shutdown_timer

    if shutdown_confirmed:
        return  # Already shutting down

    if shutdown_requested:
        # Second Ctrl+C - confirm shutdown
        shutdown_confirmed = True
        if shutdown_timer:
            shutdown_timer.cancel()
        print("\n\nShutting down...")
        logger.info("Shutdown confirmed by user")
        cleanup_all_terminals()
        os._exit(0)
    else:
        # First Ctrl+C - ask for confirmation
        shutdown_requested = True
        print("\n\nInterrupt received. Press Ctrl+C again within 5 seconds to exit, or wait to resume...")
        logger.info("Shutdown requested - waiting for confirmation")

        def reset_shutdown():
            global shutdown_requested
            if not shutdown_confirmed:
                shutdown_requested = False
                print("\nResuming server... (press Ctrl+C twice to exit)")
                logger.info("Shutdown cancelled - resuming")

        shutdown_timer = threading.Timer(5.0, reset_shutdown)
        shutdown_timer.start()

def cleanup_all_terminals():
    """Clean up all terminal resources on shutdown."""
    for sid in list(terminals.keys()):
        for term_id, terminal in list(terminals.get(sid, {}).items()):
            try:
                os.close(terminal['fd'])
            except:
                pass
            try:
                os.kill(terminal['pid'], signal.SIGTERM)
            except:
                pass
    logger.info("All terminals cleaned up")

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

# Track active PTY sessions: {sid: {termId: {'fd': fd, 'pid': pid, ...}}}
terminals = {}

# --- HTTP Routes ---

@app.route('/login')
def login():
    """Login page for token entry."""
    next_url = request.args.get('next', '/')
    error = request.args.get('error', '')
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Tmux Workspace - Login</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #1e1e1e;
                color: #cccccc;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }}
            .login-box {{
                background: #252526;
                padding: 40px;
                border-radius: 8px;
                text-align: center;
                max-width: 400px;
            }}
            h1 {{ color: #ffffff; margin-bottom: 10px; }}
            p {{ color: #888888; margin-bottom: 20px; }}
            input[type="text"] {{
                width: 100%;
                padding: 12px;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                background: #1e1e1e;
                color: #ffffff;
                font-size: 16px;
                margin-bottom: 16px;
                box-sizing: border-box;
            }}
            input[type="text"]:focus {{
                outline: none;
                border-color: #0e639c;
            }}
            button {{
                width: 100%;
                padding: 12px;
                background: #0e639c;
                color: #ffffff;
                border: none;
                border-radius: 4px;
                font-size: 16px;
                cursor: pointer;
            }}
            button:hover {{ background: #1177bb; }}
            .error {{ color: #f44747; margin-bottom: 16px; }}
        </style>
    </head>
    <body>
        <div class="login-box">
            <h1>Tmux Workspace</h1>
            <p>Enter your access token to continue</p>
            {"<p class='error'>Invalid token</p>" if error else ""}
            <form method="POST" action="/auth">
                <input type="hidden" name="next" value="{next_url}">
                <input type="text" name="token" placeholder="Access token" autofocus>
                <button type="submit">Login</button>
            </form>
        </div>
    </body>
    </html>
    '''

@app.route('/auth', methods=['POST'])
def auth():
    """Handle token submission."""
    token = request.form.get('token', '')
    next_url = request.form.get('next', '/')

    if token == ACCESS_TOKEN:
        response = make_response(redirect(next_url))
        response.set_cookie('tmux_token', token, httponly=True, samesite='Lax', max_age=86400*30)  # 30 days
        logger.info(f"Successful login from {request.remote_addr}")
        return response
    else:
        logger.warning(f"Failed login attempt from {request.remote_addr}")
        return redirect(f'/login?next={next_url}&error=1')

@app.route('/logout')
def logout():
    """Clear token cookie."""
    response = make_response(redirect('/login'))
    response.delete_cookie('tmux_token')
    return response

@app.route('/')
@token_required
def index():
    # Set cookie if authenticated via URL token (for convenience)
    response = make_response(render_template('index.html'))
    if request.args.get('token') == ACCESS_TOKEN:
        response.set_cookie('tmux_token', ACCESS_TOKEN, httponly=True, samesite='Lax', max_age=86400*30)
    return response

# --- Group API ---

@app.route('/api/groups', methods=['GET'])
@token_required
def get_groups():
    """List all groups."""
    groups = database.get_groups()
    active = database.get_active_group()
    return jsonify({'groups': groups, 'activeGroup': active})

@app.route('/api/groups', methods=['POST'])
@token_required
def create_group():
    """Create a new group."""
    data = request.get_json()
    name = data.get('name', 'New Group')
    group_id = database.create_group(name)
    return jsonify({'id': group_id, 'name': name})

@app.route('/api/groups/<int:group_id>', methods=['PUT'])
@token_required
def update_group(group_id):
    """Rename a group."""
    data = request.get_json()
    name = data.get('name')
    if name:
        database.rename_group(group_id, name)
    return jsonify({'status': 'ok'})

@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
@token_required
def delete_group(group_id):
    """Delete a group."""
    database.delete_group(group_id)
    return jsonify({'status': 'ok'})

@app.route('/api/groups/reorder', methods=['POST'])
@token_required
def reorder_groups():
    """Reorder groups."""
    data = request.get_json()
    group_ids = data.get('order', [])
    database.reorder_groups(group_ids)
    return jsonify({'status': 'ok'})

@app.route('/api/groups/active', methods=['POST'])
@token_required
def set_active_group():
    """Set the active group."""
    data = request.get_json()
    group_id = data.get('groupId')
    if group_id is not None:
        database.set_active_group(group_id)
    return jsonify({'status': 'ok'})

# --- Layout API (per group) ---

@app.route('/api/groups/<int:group_id>/layout', methods=['GET'])
@token_required
def get_group_layout(group_id):
    """Get saved layout for a group."""
    layout = database.get_layout(group_id)
    return jsonify(layout or {})

@app.route('/api/groups/<int:group_id>/layout', methods=['POST'])
@token_required
def save_group_layout(group_id):
    """Save layout for a group."""
    data = request.get_json()
    if data:
        database.save_layout(group_id, data)
    return jsonify({'status': 'ok'})

@app.route('/api/groups/<int:group_id>/layout', methods=['DELETE'])
@token_required
def delete_group_layout(group_id):
    """Delete layout for a group."""
    database.delete_layout(group_id)
    return jsonify({'status': 'ok'})

# --- Tmux Sessions API ---

@app.route('/api/sessions')
@token_required
def get_sessions():
    """List all tmux sessions."""
    try:
        result = subprocess.run(
            ['tmux', 'list-sessions', '-F', '#{session_name}:#{session_windows}:#{session_attached}'],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            print(f"tmux list-sessions failed: {result.stderr}")
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
    except subprocess.TimeoutExpired:
        print("tmux list-sessions timed out")
        return jsonify([])
    except Exception as e:
        print(f"Error listing sessions: {e}")
        return jsonify([])

@app.route('/api/sessions/<session>/windows')
@token_required
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

# --- Socket.IO Events ---

@socketio.on('connect')
def on_connect():
    # Check token from cookie (Socket.IO sends cookies with handshake)
    if TOKEN_ENABLED:
        token = request.cookies.get('tmux_token')
        if token != ACCESS_TOKEN:
            logger.warning(f"Socket.IO connection rejected - invalid token from {request.remote_addr}")
            disconnect()
            return False
    logger.info(f"Client connected: {request.sid}")
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
    term_id = data.get('termId', 'default')
    terminal_type = data.get('type', 'bash')
    session = data.get('session')
    window = data.get('window', 0)

    print(f"open_terminal: sid={sid}, termId={term_id}, type={terminal_type}")

    # Clean up existing terminal with same termId
    cleanup_terminal(sid, term_id)

    # Initialize sid dict if needed
    if sid not in terminals:
        terminals[sid] = {}

    try:
        # Create PTY
        pid, fd = pty.fork()

        if pid == 0:
            # Child process
            os.environ['TERM'] = 'xterm-256color'
            if terminal_type == 'tmux' and session:
                os.execlp('tmux', 'tmux', 'attach-session', '-t', f'{session}:{window}')
            else:
                # Spawn bash with login shell for proper env
                shell = os.environ.get('SHELL', '/bin/bash')
                os.execlp(shell, shell, '-l')
        else:
            # Parent process
            # Set non-blocking
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            terminals[sid][term_id] = {
                'fd': fd,
                'pid': pid,
                'type': terminal_type,
                'session': session,
                'window': window
            }

            # Start reading from PTY
            socketio.start_background_task(read_pty, sid, term_id, fd)
            emit('terminal_ready', {'status': 'ok', 'termId': term_id})
            print(f"Terminal ready: sid={sid}, termId={term_id}")

    except Exception as e:
        print(f"Error opening terminal: {e}")
        emit('terminal_error', {'error': str(e), 'termId': term_id})

@socketio.on('terminal_input')
def on_terminal_input(data):
    """Send input to terminal."""
    sid = request.sid
    term_id = data.get('termId', 'default') if isinstance(data, dict) else 'default'
    input_data = data.get('data', data) if isinstance(data, dict) else data

    if sid not in terminals or term_id not in terminals[sid]:
        return

    fd = terminals[sid][term_id]['fd']
    try:
        os.write(fd, input_data.encode('utf-8'))
    except Exception as e:
        print(f"Error writing to PTY: {e}")

@socketio.on('terminal_resize')
def on_terminal_resize(data):
    """Resize terminal."""
    sid = request.sid
    term_id = data.get('termId', 'default')

    if sid not in terminals or term_id not in terminals[sid]:
        return

    fd = terminals[sid][term_id]['fd']
    rows = data.get('rows', 24)
    cols = data.get('cols', 80)

    try:
        winsize = struct.pack('HHHH', rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except Exception as e:
        print(f"Error resizing PTY: {e}")

def read_pty(sid, term_id, fd):
    """Background task to read from PTY and send to client."""
    while sid in terminals and term_id in terminals[sid] and terminals[sid][term_id]['fd'] == fd:
        try:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                try:
                    data = os.read(fd, 4096)
                    if data:
                        socketio.emit('terminal_output', {
                            'termId': term_id,
                            'data': data.decode('utf-8', errors='replace')
                        }, to=sid)
                    else:
                        # EOF
                        break
                except OSError:
                    break
        except Exception as e:
            print(f"Error reading from PTY: {e}")
            break

    # Terminal closed
    socketio.emit('terminal_closed', {'termId': term_id}, to=sid)
    cleanup_terminal(sid, term_id)

def cleanup_terminal(sid, term_id=None):
    """Clean up terminal resources. If term_id is None, clean all for sid."""
    if sid not in terminals:
        return

    if term_id is not None:
        # Clean specific terminal
        terminal = terminals[sid].pop(term_id, None)
        if terminal:
            try:
                os.close(terminal['fd'])
            except:
                pass
            try:
                os.kill(terminal['pid'], signal.SIGTERM)
            except:
                pass
        # Remove sid if no more terminals
        if not terminals[sid]:
            del terminals[sid]
    else:
        # Clean all terminals for sid
        for tid, terminal in list(terminals[sid].items()):
            try:
                os.close(terminal['fd'])
            except:
                pass
            try:
                os.kill(terminal['pid'], signal.SIGTERM)
            except:
                pass
        del terminals[sid]

def main():
    """Main entry point for tmux-workspace command."""
    import argparse
    global TOKEN_ENABLED

    parser = argparse.ArgumentParser(
        prog='tmux-workspace',
        description='Web-based terminal manager with tmux integration'
    )
    parser.add_argument('--port', '-p', type=int, default=5002,
                        help='Port to run on (default: 5002)')
    parser.add_argument('--host', '-H', default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--no-token', action='store_true',
                        help='Disable access token authentication')
    parser.add_argument('--version', '-v', action='version',
                        version='%(prog)s 2.0.0')
    args = parser.parse_args()

    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, handle_shutdown)

    # Set token enabled flag
    TOKEN_ENABLED = not args.no_token

    # Print startup banner
    print("\n" + "=" * 60)
    print("  Tmux Workspace")
    print("=" * 60)
    print(f"\n  Server running at:")
    print(f"    http://localhost:{args.port}/")
    print(f"    http://127.0.0.1:{args.port}/")
    if not args.no_token:
        print(f"\n  Access token: {ACCESS_TOKEN}")
        print(f"\n  Or open:")
        print(f"    http://localhost:{args.port}/?token={ACCESS_TOKEN}")
    print(f"\n  Log file: {LOG_FILE}")
    print("\n  Press Ctrl+C twice to stop the server")
    print("=" * 60 + "\n")

    logger.info(f"Server starting on {args.host}:{args.port}")
    if not args.no_token:
        logger.info(f"Access token: {ACCESS_TOKEN}")

    # Run with debug=False to prevent double signal handlers
    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()

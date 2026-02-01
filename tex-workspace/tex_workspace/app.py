import os
import sys
import pty
import select
import subprocess
import struct
import fcntl
import termios
import signal
import secrets
import logging
import mimetypes
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_file, abort
from flask_socketio import SocketIO, emit
from . import database

# --- Logging Setup ---
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f'tex-workspace-{datetime.now().strftime("%Y%m%d")}.log')

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = secrets.token_hex(32)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*')

# Track active PTY sessions: {sid: {termId: {'fd': fd, 'pid': pid, ...}}}
terminals = {}

# Allowed file extensions for the file browser
ALLOWED_EXTENSIONS = {
    '.tex', '.bib', '.sty', '.cls', '.txt', '.md',  # Text files
    '.pdf',  # PDF files
    '.jpg', '.jpeg', '.png', '.gif', '.svg',  # Images
}

def is_allowed_file(filename):
    """Check if file extension is in allowed list."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS

def is_text_file(filename):
    """Check if file is a text file we can edit."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in {'.tex', '.bib', '.sty', '.cls', '.txt', '.md'}

def is_pdf_file(filename):
    """Check if file is a PDF."""
    return filename.lower().endswith('.pdf')

def is_image_file(filename):
    """Check if file is an image."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.svg'}

# --- HTTP Routes ---

@app.route('/')
def index():
    return render_template('index.html')

# --- File Browser API ---

@app.route('/api/files')
def list_files():
    """List directory contents with filtering."""
    path = request.args.get('path', '')
    root = database.get_root_directory()

    if not root:
        return jsonify({'error': 'No directory opened', 'files': []})

    # Resolve path relative to root
    if path:
        full_path = os.path.normpath(os.path.join(root, path))
        # Security: ensure path is within root
        if not full_path.startswith(root):
            return jsonify({'error': 'Invalid path', 'files': []}), 403
    else:
        full_path = root

    if not os.path.isdir(full_path):
        return jsonify({'error': 'Not a directory', 'files': []}), 404

    files = []
    try:
        for name in sorted(os.listdir(full_path)):
            # Skip hidden files
            if name.startswith('.'):
                continue

            item_path = os.path.join(full_path, name)
            rel_path = os.path.relpath(item_path, root)

            is_dir = os.path.isdir(item_path)

            # Filter: show directories or allowed file types
            if not is_dir and not is_allowed_file(name):
                continue

            files.append({
                'name': name,
                'path': rel_path,
                'isDirectory': is_dir,
                'isText': not is_dir and is_text_file(name),
                'isPdf': not is_dir and is_pdf_file(name),
                'isImage': not is_dir and is_image_file(name),
            })

        # Sort: directories first, then files alphabetically
        files.sort(key=lambda x: (not x['isDirectory'], x['name'].lower()))

    except PermissionError:
        return jsonify({'error': 'Permission denied', 'files': []}), 403

    return jsonify({
        'path': path,
        'root': root,
        'files': files
    })

@app.route('/api/file')
def read_file():
    """Read file content."""
    path = request.args.get('path', '')
    root = database.get_root_directory()

    if not root or not path:
        return jsonify({'error': 'Invalid request'}), 400

    full_path = os.path.normpath(os.path.join(root, path))

    # Security: ensure path is within root
    if not full_path.startswith(root):
        return jsonify({'error': 'Invalid path'}), 403

    if not os.path.isfile(full_path):
        return jsonify({'error': 'File not found'}), 404

    if is_text_file(full_path):
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({
                'path': path,
                'content': content,
                'type': 'text'
            })
        except UnicodeDecodeError:
            return jsonify({'error': 'Cannot read file as text'}), 400
    else:
        return jsonify({'error': 'Not a text file'}), 400

@app.route('/api/file', methods=['POST'])
def save_file():
    """Save file content."""
    data = request.get_json()
    path = data.get('path', '')
    content = data.get('content', '')
    root = database.get_root_directory()

    if not root or not path:
        return jsonify({'error': 'Invalid request'}), 400

    full_path = os.path.normpath(os.path.join(root, path))

    # Security: ensure path is within root
    if not full_path.startswith(root):
        return jsonify({'error': 'Invalid path'}), 403

    if not is_text_file(full_path):
        return jsonify({'error': 'Not a text file'}), 400

    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"Saved file: {path}")
        return jsonify({'status': 'ok', 'path': path})
    except Exception as e:
        logger.error(f"Error saving file: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/raw/<path:filepath>')
def serve_raw_file(filepath):
    """Serve raw file (PDF, images) for viewing."""
    root = database.get_root_directory()

    if not root:
        abort(404)

    full_path = os.path.normpath(os.path.join(root, filepath))

    # Security: ensure path is within root
    if not full_path.startswith(root):
        abort(403)

    if not os.path.isfile(full_path):
        abort(404)

    # Determine mime type
    mime_type, _ = mimetypes.guess_type(full_path)
    if mime_type is None:
        mime_type = 'application/octet-stream'

    return send_file(full_path, mimetype=mime_type)

# --- Directory Management API ---

@app.route('/api/open-directory', methods=['POST'])
def open_directory():
    """Set the root directory."""
    data = request.get_json()
    path = data.get('path', '')

    if not path:
        return jsonify({'error': 'No path provided'}), 400

    # Expand ~ to home directory
    path = os.path.expanduser(path)
    path = os.path.abspath(path)

    if not os.path.isdir(path):
        return jsonify({'error': 'Directory not found'}), 404

    database.set_root_directory(path)
    logger.info(f"Opened directory: {path}")

    return jsonify({
        'status': 'ok',
        'path': path
    })

@app.route('/api/current-directory')
def get_current_directory():
    """Get the current root directory."""
    root = database.get_root_directory()
    return jsonify({
        'path': root,
        'name': os.path.basename(root) if root else None
    })

@app.route('/api/recent-directories')
def get_recent_directories():
    """Get recently opened directories."""
    directories = database.get_recent_directories()
    return jsonify({'directories': directories})

# --- Layout API ---

@app.route('/api/layout')
def get_layout():
    """Get saved layout."""
    layout = database.get_layout()
    return jsonify(layout or {})

@app.route('/api/layout', methods=['POST'])
def save_layout():
    """Save layout."""
    data = request.get_json()
    if data:
        database.save_layout(data)
    return jsonify({'status': 'ok'})

# --- File Watching API ---

@app.route('/api/file-mtime')
def get_file_mtime():
    """Get file modification time for auto-reload detection."""
    path = request.args.get('path', '')
    root = database.get_root_directory()

    if not root or not path:
        return jsonify({'mtime': None})

    full_path = os.path.normpath(os.path.join(root, path))

    if not full_path.startswith(root):
        return jsonify({'mtime': None})

    if not os.path.isfile(full_path):
        return jsonify({'mtime': None})

    try:
        mtime = os.path.getmtime(full_path)
        return jsonify({'mtime': mtime, 'path': path})
    except:
        return jsonify({'mtime': None})

# --- Tmux Sessions API ---

@app.route('/api/sessions')
def get_sessions():
    """List all tmux sessions."""
    try:
        result = subprocess.run(
            ['tmux', 'list-sessions', '-F', '#{session_name}:#{session_windows}:#{session_attached}'],
            capture_output=True, text=True, timeout=5
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
        logger.error(f"Error listing sessions: {e}")
        return jsonify([])

# --- Socket.IO Events for Terminal ---

@socketio.on('connect')
def on_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def on_disconnect():
    sid = request.sid
    logger.info(f"Client disconnected: {sid}")
    cleanup_terminal(sid)

@socketio.on('open_terminal')
def on_open_terminal(data):
    """Open a new terminal (tmux session or bash)."""
    sid = request.sid
    term_id = data.get('termId', 'default')
    terminal_type = data.get('type', 'bash')
    session = data.get('session')
    window = data.get('window', 0)

    logger.info(f"open_terminal: sid={sid}, termId={term_id}, type={terminal_type}")

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
            # Change to project directory if set
            root = database.get_root_directory()
            if root and os.path.isdir(root):
                os.chdir(root)

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
            logger.info(f"Terminal ready: sid={sid}, termId={term_id}")

    except Exception as e:
        logger.error(f"Error opening terminal: {e}")
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
        logger.error(f"Error writing to PTY: {e}")

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
        logger.error(f"Error resizing PTY: {e}")

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
            logger.error(f"Error reading from PTY: {e}")
            break

    # Terminal closed
    socketio.emit('terminal_closed', {'termId': term_id}, to=sid)
    cleanup_terminal(sid, term_id)

def cleanup_terminal(sid, term_id=None):
    """Clean up terminal resources."""
    if sid not in terminals:
        return

    if term_id is not None:
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
        if not terminals[sid]:
            del terminals[sid]
    else:
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

# --- Main Entry Point ---

def main():
    """Main entry point for tex-workspace command."""
    import argparse

    parser = argparse.ArgumentParser(
        prog='tex-workspace',
        description='Web-based LaTeX IDE with file browser, CodeMirror editor, PDF viewer, and terminal'
    )
    parser.add_argument('--port', '-p', type=int, default=5010,
                        help='Port to run on (default: 5010)')
    parser.add_argument('--host', '-H', default='0.0.0.0',
                        help='Host to bind to (default: 0.0.0.0)')
    parser.add_argument('--directory', '-d', default=None,
                        help='Directory to open on startup')
    parser.add_argument('--version', '-v', action='version',
                        version='%(prog)s 1.0.0')
    args = parser.parse_args()

    # Set initial directory if provided
    if args.directory:
        path = os.path.expanduser(args.directory)
        path = os.path.abspath(path)
        if os.path.isdir(path):
            database.set_root_directory(path)
            logger.info(f"Opening directory: {path}")

    # Print startup banner
    print("\n" + "=" * 60)
    print("  TeX Workspace - LaTeX IDE")
    print("=" * 60)
    print(f"\n  Server running at:")
    print(f"    http://localhost:{args.port}/")
    print(f"    http://127.0.0.1:{args.port}/")
    print(f"\n  Log file: {LOG_FILE}")
    print("\n  Press Ctrl+C to stop the server")
    print("=" * 60 + "\n")

    logger.info(f"Server starting on {args.host}:{args.port}")

    socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tex_workspace.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()

    # Layouts table (stores GoldenLayout config)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL DEFAULT 'default',
            config TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # Settings table (key-value store)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    # Recent directories
    conn.execute('''
        CREATE TABLE IF NOT EXISTS recent_directories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            last_opened TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()

# --- Layout operations ---

def save_layout(config, name='default'):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    config_json = json.dumps(config)

    existing = conn.execute(
        'SELECT id FROM layouts WHERE name = ?', (name,)
    ).fetchone()

    if existing:
        conn.execute(
            'UPDATE layouts SET config = ?, updated_at = ? WHERE name = ?',
            (config_json, now, name)
        )
    else:
        conn.execute(
            'INSERT INTO layouts (name, config, updated_at) VALUES (?, ?, ?)',
            (name, config_json, now)
        )

    conn.commit()
    conn.close()

def get_layout(name='default'):
    conn = get_connection()
    row = conn.execute(
        'SELECT config FROM layouts WHERE name = ?', (name,)
    ).fetchone()
    conn.close()

    if row:
        return json.loads(row['config'])
    return None

def delete_layout(name='default'):
    conn = get_connection()
    conn.execute('DELETE FROM layouts WHERE name = ?', (name,))
    conn.commit()
    conn.close()

# --- Settings ---

def get_setting(key, default=None):
    conn = get_connection()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default

def set_setting(key, value):
    conn = get_connection()
    conn.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?',
        (key, value, value)
    )
    conn.commit()
    conn.close()

def get_root_directory():
    return get_setting('root_directory')

def set_root_directory(path):
    set_setting('root_directory', path)
    add_recent_directory(path)

# --- Recent directories ---

def add_recent_directory(path):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute(
        'INSERT INTO recent_directories (path, last_opened) VALUES (?, ?) '
        'ON CONFLICT(path) DO UPDATE SET last_opened = ?',
        (path, now, now)
    )
    conn.commit()
    conn.close()

def get_recent_directories(limit=10):
    conn = get_connection()
    rows = conn.execute(
        'SELECT path FROM recent_directories ORDER BY last_opened DESC LIMIT ?',
        (limit,)
    ).fetchall()
    conn.close()
    return [row['path'] for row in rows]

# Initialize database on import
init_db()

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'terminals.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()

    # Groups table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')

    # Layouts table (now linked to groups)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            config TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        )
    ''')

    # Active group tracking
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    conn.commit()
    conn.close()

# --- Group operations ---

def get_groups():
    conn = get_connection()
    rows = conn.execute('SELECT * FROM groups ORDER BY position').fetchall()
    conn.close()
    return [dict(row) for row in rows]

def create_group(name):
    conn = get_connection()
    now = datetime.utcnow().isoformat()

    # Get next position
    max_pos = conn.execute('SELECT MAX(position) FROM groups').fetchone()[0]
    position = (max_pos or 0) + 1

    cursor = conn.execute(
        'INSERT INTO groups (name, position, created_at, updated_at) VALUES (?, ?, ?, ?)',
        (name, position, now, now)
    )
    group_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return group_id

def rename_group(group_id, new_name):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    conn.execute(
        'UPDATE groups SET name = ?, updated_at = ? WHERE id = ?',
        (new_name, now, group_id)
    )
    conn.commit()
    conn.close()

def delete_group(group_id):
    conn = get_connection()
    conn.execute('DELETE FROM layouts WHERE group_id = ?', (group_id,))
    conn.execute('DELETE FROM groups WHERE id = ?', (group_id,))
    conn.commit()
    conn.close()

def reorder_groups(group_ids):
    """Update positions based on new order."""
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    for i, gid in enumerate(group_ids):
        conn.execute(
            'UPDATE groups SET position = ?, updated_at = ? WHERE id = ?',
            (i, now, gid)
        )
    conn.commit()
    conn.close()

# --- Layout operations (per group) ---

def save_layout(group_id, config):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    config_json = json.dumps(config)

    # Check if layout exists for this group
    existing = conn.execute(
        'SELECT id FROM layouts WHERE group_id = ?', (group_id,)
    ).fetchone()

    if existing:
        conn.execute(
            'UPDATE layouts SET config = ?, updated_at = ? WHERE group_id = ?',
            (config_json, now, group_id)
        )
    else:
        conn.execute(
            'INSERT INTO layouts (group_id, config, updated_at) VALUES (?, ?, ?)',
            (group_id, config_json, now)
        )

    conn.commit()
    conn.close()

def get_layout(group_id):
    conn = get_connection()
    row = conn.execute(
        'SELECT config FROM layouts WHERE group_id = ?', (group_id,)
    ).fetchone()
    conn.close()

    if row:
        return json.loads(row['config'])
    return None

def delete_layout(group_id):
    conn = get_connection()
    conn.execute('DELETE FROM layouts WHERE group_id = ?', (group_id,))
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

def get_active_group():
    val = get_setting('active_group')
    return int(val) if val else None

def set_active_group(group_id):
    set_setting('active_group', str(group_id))

# Initialize database on import
init_db()

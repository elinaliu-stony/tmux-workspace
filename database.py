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
    conn.execute('''
        CREATE TABLE IF NOT EXISTS layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL DEFAULT 'default',
            config TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_layout(config, name='default'):
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    config_json = json.dumps(config)

    conn.execute('''
        INSERT INTO layouts (name, config, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            config = excluded.config,
            updated_at = excluded.updated_at
    ''', (name, config_json, now))
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

# Initialize database on import
init_db()

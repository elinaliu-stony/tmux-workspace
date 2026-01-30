#!/usr/bin/env python3
"""Migrate layout from v1 (port 5001) to v2 (port 5002)."""

import sqlite3
import json
import os
from datetime import datetime

V1_DB = os.path.expanduser('~/workspace/tmux-workspace/terminals.db')
V2_DB = os.path.join(os.path.dirname(__file__), 'terminals.db')

def migrate():
    if not os.path.exists(V1_DB):
        print(f"V1 database not found: {V1_DB}")
        return

    # Read v1 layout
    v1_conn = sqlite3.connect(V1_DB)
    v1_conn.row_factory = sqlite3.Row
    row = v1_conn.execute("SELECT config FROM layouts WHERE name = 'default'").fetchone()
    v1_conn.close()

    if not row:
        print("No layout found in v1 database")
        return

    v1_config = json.loads(row['config'])
    print(f"Found v1 layout with {len(str(v1_config))} chars")

    # Initialize v2 database
    import database
    database.init_db()

    # Create a group for the migrated layout
    now = datetime.utcnow().isoformat()
    v2_conn = sqlite3.connect(V2_DB)

    # Check if already migrated
    existing = v2_conn.execute("SELECT id FROM groups WHERE name = 'Default'").fetchone()
    if existing:
        group_id = existing[0]
        print(f"Default group already exists (id={group_id}), updating layout...")
    else:
        cursor = v2_conn.execute(
            "INSERT INTO groups (name, position, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ('Default', 0, now, now)
        )
        group_id = cursor.lastrowid
        print(f"Created Default group (id={group_id})")

    # Save layout for this group
    config_json = json.dumps(v1_config)
    existing_layout = v2_conn.execute(
        "SELECT id FROM layouts WHERE group_id = ?", (group_id,)
    ).fetchone()

    if existing_layout:
        v2_conn.execute(
            "UPDATE layouts SET config = ?, updated_at = ? WHERE group_id = ?",
            (config_json, now, group_id)
        )
    else:
        v2_conn.execute(
            "INSERT INTO layouts (group_id, config, updated_at) VALUES (?, ?, ?)",
            (group_id, config_json, now)
        )

    # Set as active group
    v2_conn.execute(
        "INSERT INTO settings (key, value) VALUES ('active_group', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = ?",
        (str(group_id), str(group_id))
    )

    v2_conn.commit()
    v2_conn.close()

    print("Migration complete!")
    print(f"Your v1 layout has been imported into the 'Default' group in v2")

if __name__ == '__main__':
    migrate()

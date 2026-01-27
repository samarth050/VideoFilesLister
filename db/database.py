import sqlite3
import os
from db.schema import FILES_TABLE_SQL, FILES_TABLE_INDEX, CATEGORIES_TABLE_SQL, DB_SELECT_ALL

def init_db(db_path, fresh=False):
        """
        Initialize database.
        fresh=True → drops Files and Categories tables and recreates them (CLEAN RESET).
        """

        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        if fresh:
            cur.execute("DROP TABLE IF EXISTS Files")
            cur.execute("DROP TABLE IF EXISTS Categories")

        cur.execute(FILES_TABLE_SQL)
        cur.execute(FILES_TABLE_INDEX)
        cur.execute(CATEGORIES_TABLE_SQL)

        conn.commit()
        conn.close()

def ensure_global_unique_index(db_path):
    if not db_path:
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create unique index safely
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_file_global
        ON Files (file_name, size_bytes);
    """)

    conn.commit()
    conn.close()

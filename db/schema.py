FILES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS Files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,

    storage_id TEXT NOT NULL,
    full_path TEXT NOT NULL,
    creation_date TEXT,

    year INTEGER,
    category TEXT,

    added_on TEXT DEFAULT CURRENT_TIMESTAMP,
    file_hash TEXT,

    UNIQUE(file_name, size_bytes)
);
"""
FILES_TABLE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_file_global
ON Files (file_name, size_bytes);
"""
CATEGORIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS Categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
"""


DB_SELECT_ALL = """
SELECT id, file_name, extension, size_bytes, storage_id,
       creation_date, full_path, year, category
FROM Files
ORDER BY id DESC
"""
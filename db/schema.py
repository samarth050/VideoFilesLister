# Movie Detail Queries
SELECT_MOVIE_METADATA_FULL = """
SELECT category, description, cover1_path, cover2_path, metadata_url
FROM MovieDetails
WHERE file_id = ?
"""

SELECT_MOVIE_METADATA = """
SELECT category, description, cover1_path, cover2_path
FROM MovieDetails
WHERE file_id = ?
"""
# File Operations
UPDATE_FILES_CATEGORY = """
UPDATE Files
SET category = ?
WHERE id = ?
"""

UPDATE_FILE_MOVE = """
UPDATE Files
SET storage_id = ?, full_path = ?, creation_date = ?
WHERE id = ?
"""

INSERT_FILE_RECORD = """
INSERT INTO Files
(file_name, extension, size_bytes, storage_id,
 creation_date, full_path, year, category)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

DELETE_FILE_BY_ID = """
DELETE FROM Files
WHERE id = ?
"""

# Statistic Queries
SELECT_STORAGE_STATS = """
SELECT storage_id,
       COUNT(*) AS cnt,
       SUM(size_bytes) AS total_size
FROM Files
GROUP BY storage_id
ORDER BY storage_id
"""

SELECT_TOTAL_COUNT = "SELECT COUNT(*) FROM Files"

SELECT_TOTAL_SIZE = "SELECT IFNULL(SUM(size_bytes),0) FROM Files"

SELECT_EXTENSION_STATS = """
SELECT extension,
       COUNT(*) AS cnt,
       SUM(size_bytes) AS total_size
FROM Files
GROUP BY extension
ORDER BY extension
"""

# Category Queries
SELECT_ALL_CATEGORIES = """
SELECT name
FROM Categories
ORDER BY name
"""

INSERT_CATEGORY = """
INSERT OR IGNORE INTO Categories(name)
VALUES (?)
"""

SELECT_DISTINCT_FILE_CATEGORIES = """
SELECT DISTINCT category
FROM Files
ORDER BY category
"""


MOVIE_DETAILS_INSERT = """
 INSERT INTO MovieDetails
 (file_id, category, description, cover1_path, cover2_path, metadata_url)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(file_id) DO UPDATE SET
        category=excluded.category,
        description=excluded.description,
        cover1_path=excluded.cover1_path,
        cover2_path=excluded.cover2_path,
        metadata_url=excluded.metadata_url
"""
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
MOVIE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS MovieDetails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER UNIQUE,

    category TEXT,
    description TEXT,
    cover1_path TEXT,
    cover2_path TEXT,
    metadata_url TEXT,

    FOREIGN KEY (file_id) REFERENCES Files(id) ON DELETE CASCADE
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
SELECT
    f.id,
    f.file_name,
    f.extension,
    f.size_bytes,
    f.storage_id,
    f.creation_date,
    f.full_path,
    f.year,
    COALESCE(m.category, f.category) AS category
FROM Files f
LEFT JOIN MovieDetails m
    ON f.id = m.file_id
ORDER BY f.id DESC
"""
DB_SELECT_STORAGE_ID = """
SELECT
    f.id,
    f.file_name,
    f.extension,
    f.size_bytes,
    f.storage_id,
    f.creation_date,
    f.full_path,
    f.year,
    COALESCE(m.category, f.category) AS category
FROM Files f
LEFT JOIN MovieDetails m
    ON f.id = m.file_id
WHERE f.storage_id = ?
ORDER BY f.id DESC
"""

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
    movie_name TEXT,
    year TEXT,
    category TEXT,
    description TEXT,
    image1_url TEXT,
    image2_url TEXT,
    FOREIGN KEY (file_id) REFERENCES Files(id)
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

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
# Movie Details View
DROP_METADATA_VIEW = """
DROP VIEW IF EXISTS MetadataStatusView;
"""

CREATE_METADATA_VIEW = """
CREATE VIEW MetadataStatusView AS
SELECT
    f.id,
    f.file_name,
    f.extension,
    f.size_bytes,
    f.storage_id,
    f.creation_date,
    f.full_path,
    f.year,

    COALESCE(m.category, f.category) AS category,

    CASE
        WHEN m.file_id IS NULL
            THEN 'NO_METADATA'

        WHEN
            (m.category IS NULL OR m.category='')
         OR (m.description IS NULL OR m.description='')
         OR (m.cover1_path IS NULL OR m.cover1_path='')
         OR (m.cover2_path IS NULL OR m.cover2_path='')
            THEN 'INCOMPLETE'

        ELSE 'COMPLETE'
    END AS metadata_status

FROM Files f
LEFT JOIN MovieDetails m
ON f.id = m.file_id
"""
METADATA_STATUS_STATS = """
SELECT
COUNT(*) AS total_files,

SUM(CASE WHEN v.metadata_status='COMPLETE' THEN 1 ELSE 0 END) AS complete_count,
SUM(CASE WHEN v.metadata_status='INCOMPLETE' THEN 1 ELSE 0 END) AS incomplete_count,
SUM(CASE WHEN v.metadata_status='NO_METADATA' THEN 1 ELSE 0 END) AS no_metadata_count,

SUM(CASE WHEN m.category IS NULL OR m.category='' THEN 1 ELSE 0 END) AS missing_category,
SUM(CASE WHEN m.description IS NULL OR m.description='' THEN 1 ELSE 0 END) AS missing_description,
SUM(CASE WHEN m.cover1_path IS NULL OR m.cover1_path='' THEN 1 ELSE 0 END) AS missing_cover1,
SUM(CASE WHEN m.cover2_path IS NULL OR m.cover2_path='' THEN 1 ELSE 0 END) AS missing_cover2

FROM Files f
LEFT JOIN MovieDetails m ON f.id = m.file_id
LEFT JOIN MetadataStatusView v ON f.id = v.id
"""
# File Operations
UPDATE_FILES_CATEGORY = """
UPDATE Files
SET category = ?
WHERE id = ?
"""
SELECT_MOVIE_DETAIL_VIEW = """
SELECT f.file_name,
       f.extension,
       f.year,
       f.storage_id,
       f.full_path,
       f.size_bytes,
       m.category,
       m.description,
       m.cover1_path,
       m.cover2_path
FROM Files f
LEFT JOIN MovieDetails m ON f.id = m.file_id
WHERE f.id = ?
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
SELECT DISTINCT TRIM(category) AS category FROM Files
WHERE TRIM(category) IS NOT NULL AND TRIM(category) != ''
UNION
SELECT DISTINCT TRIM(category) FROM MovieDetails
WHERE TRIM(category) IS NOT NULL AND TRIM(category) != ''
ORDER BY category
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
    category = excluded.category,
    description = excluded.description,
    cover1_path = excluded.cover1_path,
    cover2_path = excluded.cover2_path,
    metadata_url = excluded.metadata_url

WHERE
    MovieDetails.category IS NULL
    OR MovieDetails.description IS NULL
    OR MovieDetails.cover1_path IS NULL
    OR MovieDetails.cover2_path IS NULL
"""
MOVIE_DETAILS_INSERT_MANUAL = """
INSERT INTO MovieDetails
(file_id, category, description, cover1_path, cover2_path)
VALUES (?, ?, ?, ?, ?)

ON CONFLICT(file_id) DO UPDATE SET
    category = excluded.category,
    description = excluded.description,
    cover1_path = COALESCE(excluded.cover1_path, MovieDetails.cover1_path),
    cover2_path = COALESCE(excluded.cover2_path, MovieDetails.cover2_path)
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

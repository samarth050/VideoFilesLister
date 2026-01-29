# duplicates/duplicate_analyzer.py

import re
from collections import defaultdict


# -------------------------------
# Helpers
# -------------------------------

def normalize_name(name: str) -> str:
    """
    Used for PARTIAL MATCH detection.
    Lowercase, remove extension, replace dots/underscores with spaces,
    collapse spaces, remove non-alphanumerics.
    """
    base = name.rsplit(".", 1)[0].lower()
    base = base.replace(".", " ").replace("_", " ")
    base = re.sub(r"[^a-z0-9 ]+", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    return base


def base_name(name: str) -> str:
    """Filename without extension, lowercase"""
    return name.rsplit(".", 1)[0].lower()


def extension(name: str) -> str:
    if "." in name:
        return name.rsplit(".", 1)[1].lower()
    return ""


# -------------------------------
# Main public API
# -------------------------------

def analyze_duplicates(conn):
    """
    conn = sqlite3 connection

    RETURNS: list of groups

    Each group:
    {
        "type": "Duplicate Record" | "Two Versions Exist"
                | "Upgraded Version Exists" | "Partial Match",
        "key": string,
        "records": [ {db row dict}, {db row dict}, ... ]
    }
    """

    rows = fetch_all_files(conn)

    results = []
    used_ids = set()

    # ---------------------------------------
    # 1. Duplicate Record
    # same filename + same size
    # ---------------------------------------
    exact_map = defaultdict(list)
    for r in rows:
        key = (r["file_name"].lower(), r["size_bytes"])
        exact_map[key].append(r)

    for key, group in exact_map.items():
        if len(group) > 1:
            mark_used(group, used_ids)
            results.append(make_group("Duplicate Record", str(key), group))

    # ---------------------------------------
    # 2. Two Versions Exist
    # same filename + different size
    # ---------------------------------------
    name_map = defaultdict(list)
    for r in rows:
        if r["id"] in used_ids:
            continue
        name_map[r["file_name"].lower()].append(r)

    for name, group in name_map.items():
        sizes = {r["size_bytes"] for r in group}
        if len(group) > 1 and len(sizes) > 1:
            mark_used(group, used_ids)
            results.append(make_group("Two Versions Exist", name, group))

    # ---------------------------------------
    # 3. Upgraded Version Exists
    # same base name, different extensions
    # ---------------------------------------
    base_map = defaultdict(list)
    for r in rows:
        if r["id"] in used_ids:
            continue
        base_map[base_name(r["file_name"])].append(r)

    for base, group in base_map.items():
        exts = {extension(r["file_name"]) for r in group}
        if len(group) > 1 and len(exts) > 1:
            mark_used(group, used_ids)
            results.append(make_group("Upgraded Version Exists", base, group))

    # ---------------------------------------
    # 4. Partial Match
    # normalized name matches
    # ---------------------------------------
    norm_map = defaultdict(list)
    for r in rows:
        if r["id"] in used_ids:
            continue
        norm_map[normalize_name(r["file_name"])].append(r)

    for norm, group in norm_map.items():
        if len(group) > 1:
            mark_used(group, used_ids)
            results.append(make_group("Partial Match", norm, group))

    return results


# -------------------------------
# DB Access
# -------------------------------

def fetch_all_files(conn):
    """
    Adjust column names here ONLY if your DB schema differs.
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            id,
            file_name,
            size_bytes,
            storage_id,
            full_path,
            creation_date
        FROM Files
    """)
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


# -------------------------------
# Utilities
# -------------------------------

def mark_used(records, used_ids: set):
    for r in records:
        used_ids.add(r["id"])


def make_group(group_type, key, records):
    return {
        "type": group_type,
        "key": key,
        "records": records
    }

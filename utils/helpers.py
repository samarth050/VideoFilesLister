import datetime

def format_size(size_bytes):
    try:
        size = int(size_bytes or 0)
    except:
        return str(size_bytes)
    if size < 1024:
        return f"{size} bytes"
    if size < 1024**2:
        return f"{size/1024:.2f} KB"
    if size < 1024**3:
        return f"{size/(1024**2):.2f} MB"
    return f"{size/(1024**3):.2f} GB"

def format_date(d):
    if d is None:
        return ""
    if isinstance(d, str):
        return d
    if isinstance(d, datetime.datetime):
        return d.strftime("%Y-%m-%d %H:%M:%S")
    try:
        return datetime.datetime.fromtimestamp(float(d)).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return str(d)
    
def format_db_total_size(size_bytes):
    try:
        size = int(size_bytes or 0)
    except:
        return "0 MB"
    if size >= 1024**4:  # 1024 GB = 1 TB
        return f"{size / (1024**4):.2f} TB"
    else:
        return f"{size / (1024**2):.2f} MB"
    
def format_bytes(size):
    if not size:
        return "0 MB"
    tb = 1024 ** 4
    gb = 1024 ** 3
    mb = 1024 ** 2
    if size >= tb:
        return f"{size / tb:.2f} TB"
    elif size >= gb:
        return f"{size / gb:.2f} GB"
    else:
        return f"{size / mb:.2f} MB"    
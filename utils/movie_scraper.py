"""
movie_scraper.py
----------------
Scrapes movie metadata (name, year, category, description, images)
from supported web pages.

This module contains NO GUI code.
It is safe to import into FileLister.
"""

import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ------------------------------
# Controlled Category Vocabulary
# ------------------------------
KNOWN_CATEGORIES = [
    "Classic Porn, Sex Education",
    "Classic Porn, Incest",
    "Drama, Incest",
    "Incest, Thriller",

    "Classic Porn",
    "Classic Erotica",
    "Newage Porn",
    "Newage Erotica",
    "Asian Erotica",

    "Incest",
    "Sex Education",

    "Action",
    "Adventure",
    "Comedy",
    "Crime",
    "Drama",
    "Horror",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Thriller",

    "Asian",
]


# ------------------------------
# Public API
# ------------------------------
def scrape_movie(url, timeout=15):
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # -------- Name + Year --------
    slug = urlparse(url).path.strip("/")
    m = re.search(r"(.+)-(\d{4})$", slug)
    name = m.group(1).replace("-", " ").title() if m else slug.replace("-", " ").title()
    year = m.group(2) if m else ""

    # -------- Category (more reliable) --------
    category = ""
    meta = soup.select_one(".entry-meta")

    if meta:
        parts = [p.strip() for p in meta.get_text("|", strip=True).split("|")]

        if len(parts) >= 2:
            category = parts[1]

    # -------- Article Content --------
    content = soup.select_one(".entry-content")

    # -------- Description --------
    description = ""

    if content:
        for p in content.find_all("p"):
            txt = p.get_text(" ", strip=True)

            if txt.lower().startswith("description"):
                description = txt.split(":",1)[-1].strip()
                break

        # fallback: longest paragraph
        if not description:
            paragraphs = [p.get_text(" ", strip=True) for p in content.find_all("p")]
            if paragraphs:
                description = max(paragraphs, key=len)

    # -------- Cover Images --------
    images = []

    if content:
        for img in content.select("img[src]"):

            src = img["src"]

            if not src.lower().endswith((".jpg",".jpeg",".png")):
                continue

            src = re.sub(r"-\d+x\d+(?=\.(jpg|jpeg|png))","",src)

            if any(x in src.lower() for x in ["logo","avatar","icon","banner","ads"]):
                continue

            if src not in images:
                images.append(src)

    images = images[:2]

    return {
        "name": name,
        "year": year,
        "category": category,
        "description": description,
        "images": images
    }
"""def scrape_movie(url, timeout=15):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": url
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    full_text = soup.get_text(separator=" ", strip=True)
    full_text = re.sub(r"\s+", " ", full_text)

    name, year = _extract_name_year_from_url(url)
    category = _extract_category(full_text)
    description = _extract_description(full_text)
    images = _extract_images(soup)

    return {
        "name": name,
        "year": year,
        "category": category,
        "description": description,
        "images": images
    }
    """


# ------------------------------
# Internal Helpers
# ------------------------------

def _extract_name_year_from_url(url):
    parsed_url = urlparse(url)
    slug = parsed_url.path.strip("/")

    match = re.search(r"(.+)-(\d{4})$", slug)

    if match:
        name_part = match.group(1)
        year = match.group(2)
        movie_name = name_part.replace("-", " ").title()
    else:
        movie_name = slug.replace("-", " ").title()
        year = ""

    return movie_name, year

def scrape_category_urls(category_url, timeout=15):
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(category_url, headers=headers, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Match movie pattern like /movie-name-1979/
        if re.match(r"https://rarelust\.com/.+-\d{4}/?$", href):
            urls.add(href.rstrip("/"))

    return list(urls)

def _extract_category(full_text):
    raw_block = _extract_raw_category_block(full_text)

    if not raw_block:
        return ""

    raw_block = raw_block.strip()

    matched = []

    # 🔥 Sort by length DESC so longer phrases match first
    for cat in sorted(KNOWN_CATEGORIES, key=len, reverse=True):
        pattern = r"\b" + re.escape(cat) + r"\b"
        if re.search(pattern, raw_block, re.IGNORECASE):

            # Prevent adding shorter category if it is part of a longer one
            if not any(cat in existing for existing in matched):
                matched.append(cat)

    if matched:
        return ", ".join(sorted(matched))

    # Fallback to raw block
    raw_parts = [p.strip() for p in raw_block.split(",") if p.strip()]

    seen = set()
    unique = []
    for part in raw_parts:
        if part.lower() not in seen:
            unique.append(part)
            seen.add(part.lower())

    return ", ".join(sorted(unique))


def _extract_raw_category_block(full_text):
    lower_text = full_text.lower()

    pipe_index = lower_text.find("|")
    dir_index = lower_text.find("directed by")

    if pipe_index != -1 and dir_index != -1 and dir_index > pipe_index:
        raw_block = full_text[pipe_index + 1:dir_index].strip()

        # Remove date pattern like: January 30, 2026
        raw_block = re.sub(
            r"[A-Za-z]+\s+\d{1,2},\s+\d{4}",
            "",
            raw_block
        )

        return raw_block.strip()

    return ""


def _extract_description(full_text):
    desc_index = full_text.lower().find("description")

    if desc_index == -1:
        return ""

    colon_index = full_text.find(":", desc_index)

    if colon_index == -1:
        return ""

    start_index = colon_index + 1
    preview_index = full_text.lower().find("preview", start_index)

    if preview_index != -1:
        return full_text[start_index:preview_index].strip()

    return full_text[start_index:].strip()


def _extract_images(soup):
    images = []

    def clean_url(url):
        # Remove WordPress thumbnail size suffix (e.g. -300x450.jpg)
        return re.sub(r'-\d+x\d+(?=\.(jpg|jpeg|png))', '', url)

    for img in soup.find_all("img"):
        parent = img.parent
        url = None

        # 1️⃣ Prefer full image from parent <a href="">
        if parent and parent.name == "a" and parent.get("href"):
            href = parent["href"]
            if href.lower().endswith((".jpg", ".jpeg", ".png")):
                url = href

        # 2️⃣ Fallback to src
        if not url:
            src = img.get("src")
            if src and src.lower().endswith((".jpg", ".jpeg", ".png")):
                url = src

        if not url:
            continue

        url = clean_url(url)

        # 3️⃣ Skip small or irrelevant images
        if any(x in url.lower() for x in [
            "logo", "avatar", "icon", "banner", "ads", "wp-content/plugins"
        ]):
            continue

        if url not in images:
            images.append(url)

    return images

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
    "Classic Porn",
    "Incest",
    "Thriller",
    "Drama",
    "Horror",
    "Comedy",
    "Action",
    "Romance",
    "Sci-Fi",
    "Adventure",
]


# ------------------------------
# Public API
# ------------------------------
def scrape_movie(url, timeout=15):
    """
    Main entry point.

    Returns:
        {
            "name": str,
            "year": str,
            "category": str,
            "description": str,
            "images": list[str]
        }

    Raises:
        Exception if page cannot be loaded.
    """

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


def _extract_category(full_text):
    raw_block = _extract_raw_category_block(full_text)

    matched = []

    for cat in KNOWN_CATEGORIES:
        pattern = r"\b" + re.escape(cat) + r"\b"
        if re.search(pattern, raw_block, re.IGNORECASE):
            matched.append(cat)

    return ", ".join(matched)


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

    article = soup.find("article")

    if article:
        img_tags = article.find_all("img")
    else:
        img_tags = soup.find_all("img")

    for img in img_tags:
        src = img.get("src") or img.get("data-src")

        if src and src.startswith("http"):
            images.append(src)

    return images[:2]

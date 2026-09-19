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
from urllib.parse import urljoin, urlparse

# ------------------------------
# Persistent HTTP session
# ------------------------------

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive"
})

# ------------------------------
# Controlled Category Vocabulary
# ------------------------------
KNOWN_CATEGORIES = [
    "Classic Porn, Sex Education",
    "Classic Porn, Incest",
    "Drama, Incest",
    "Incest, Newage Erotica",
    "Incest, Newage Porn",
    "Incest, Thriller",
    "Incest, Mystery",
    "Incest, Romance",

    "Classic Porn",
    "Classic Erotica, Incest",
    "Classic Erotica",
    "Newage Porn",
    "Newage Erotica",
    "Asian Erotica, Incest",
    "Asian Erotica",

    "Incest",
    "Sex Education",

    "Action",
    "Adventure",
    "Comedy",
    "Crime, Incest",
    "Crime",
    "Drama",
    "Documentary",
    "Fantasy",
    "Horror, Incest",
    "Horror",
    "Mystery",
    "Romance",
    "Sci-Fi",
    "Thriller",
    "Asian, Incest",
    "Asian",
]


# ------------------------------
# Public API
# ------------------------------
def extract_size_info(text):
    if not text:
        return {"size_text": "", "size_bytes": None}

    normalized = re.sub(r"\s+", " ", text).strip()
    size_pattern = re.compile(
        r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>kb|mb|gb|tb)",
        re.IGNORECASE,
    )

    for match in size_pattern.finditer(normalized):
        value = float(match.group("value").replace(",", "."))
        unit = match.group("unit").lower()
        multipliers = {"kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
        size_bytes = int(value * multipliers[unit])

        if abs(value - round(value)) < 1e-9:
            size_text = f"{int(round(value))} {unit.upper()}"
        else:
            value_text = f"{value:.2f}".rstrip("0").rstrip(".")
            size_text = f"{value_text} {unit.upper()}"

        return {"size_text": size_text, "size_bytes": size_bytes}

    return {"size_text": "", "size_bytes": None}


def scrape_movie(url, timeout=15):
    r = SESSION.get(url, timeout=timeout)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")

    # Adult Film Database has a distinct, structured page format.  Dispatch
    # only for that host so the existing Rarelust parsing remains unchanged.
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host == "adultfilmdatabase.com":
        return _scrape_adultfilmdatabase_movie(url, soup)

    # -------- Name + Year --------
    slug = urlparse(url).path.strip("/")
    m = re.search(r"(.+)-(\d{4})$", slug)
    name = m.group(1).replace("-", " ").title() if m else slug.replace("-", " ").title()
    year = m.group(2) if m else ""

    # -------- Category --------
    category = ""
    meta = soup.select_one(".entry-meta")

    if meta:
        text = meta.get_text(" ", strip=True)

        for cat in sorted(KNOWN_CATEGORIES, key=len, reverse=True):
            if re.search(r"\b" + re.escape(cat) + r"\b", text, re.IGNORECASE):
                category = cat
                break

    # -------- Article Content --------
    content = soup.select_one(".entry-content")
    candidate_texts = []

    if content:
        for p in content.find_all("p"):
            paragraph_text = p.get_text(" ", strip=True)
            if paragraph_text:
                candidate_texts.append(paragraph_text)

    full_text = soup.get_text(" ", strip=True)
    candidate_texts.append(full_text)
    size_info = extract_size_info(" \n ".join(candidate_texts))

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
        for img in content.find_all("img"):
            # WordPress pages commonly lazy-load their covers, so the actual
            # image may be in data-src rather than src.
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
            if not src:
                continue

            src = urljoin(url, src)
            image_path = urlparse(src).path.lower()
            if not image_path.endswith((".jpg", ".jpeg", ".png", ".webp")):
                continue

            src = re.sub(r"-\d+x\d+(?=\.(jpg|jpeg|png|webp)(?:$|[?#]))", "", src,
                         flags=re.IGNORECASE)

            if any(x in src.lower() for x in ["logo","avatar","icon","banner","ads"]):
                continue

            images.append(src)

    # remove duplicates
    seen=set()
    unique=[]
    for i in images:
        if i not in seen:
            unique.append(i)
            seen.add(i)

    images = unique[:2]

    return {
        "name": name,
        "year": year,
        "category": category,
        "description": description,
        "images": images,
        "size_text": size_info["size_text"],
        "size_bytes": size_info["size_bytes"],
    }


def _scrape_adultfilmdatabase_movie(url, soup):
    """Extract metadata and front/back cover art from an Adult Film Database video page."""
    title = soup.select_one("h1[itemprop='name'], h1")
    name = title.get_text(" ", strip=True) if title else ""

    # AFD places the release year after the studio name, e.g. "Studio: Private
    # (2003)".  Limit the search to that label so years in the description do
    # not override the release year.
    studio_text = ""
    for node in soup.find_all(string=re.compile(r"^\s*Studio\s*:")):
        studio_text = node.parent.get_text(" ", strip=True)
        if studio_text:
            break
    year_match = re.search(r"\b((?:19|20)\d{2})\b", studio_text)
    year = year_match.group(1) if year_match else ""

    description_node = soup.select_one("[itemprop='description']")
    description = description_node.get_text(" ", strip=True) if description_node else ""

    # Genres appear as tagged links in the panel headed "Genres".  Keep the
    # site's labels instead of forcing them into the Rarelust category list.
    category = ""
    genres_heading = soup.find(
        lambda tag: tag.name == "div" and tag.get_text(" ", strip=True) == "Genres"
    )
    if genres_heading:
        genres_container = genres_heading.find_next_sibling("div")
        genres = [
            tag.get_text(" ", strip=True)
            for tag in genres_container.select("a span")
            if tag.get_text(" ", strip=True)
        ] if genres_container else []
        category = ", ".join(dict.fromkeys(genres))

    images = []
    for image in soup.find_all("img"):
        src = image.get("data-src") or image.get("data-lazy-src") or image.get("src")
        if not src:
            continue
        absolute_src = urljoin(url, src)
        image_path = urlparse(absolute_src).path.lower()
        if not re.search(r"/graphics/boxes/[^/]+/(?:front|back)/[^/]+\.(?:jpg|jpeg|png|webp)$", image_path):
            continue
        if absolute_src not in images:
            images.append(absolute_src)

    return {
        "name": name,
        "year": year,
        "category": category,
        "description": description,
        "images": images[:2],
        "size_text": "",
        "size_bytes": None,
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

    response = SESSION.get(category_url, timeout=timeout)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    urls = set()
    source_host = urlparse(category_url).netloc.lower()

    for a in soup.find_all("a", href=True):
        href = urljoin(category_url, a["href"])
        parsed = urlparse(href)

        # Match same-site movie patterns like /movie-name-1979/
        if parsed.netloc.lower() == source_host and re.match(r"^/.+-\d{4}/?$", parsed.path):
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

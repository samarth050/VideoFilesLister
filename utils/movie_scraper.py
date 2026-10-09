"""
movie_scraper.py
----------------
Scrapes movie metadata (name, year, category, description, images)
from supported web pages.

This module contains NO GUI code.
It is safe to import into FileLister.
"""

import re
import json
import os
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
    "Animation",
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
    "Short",
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
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host == "imdb.com" or host.endswith(".imdb.com"):
        return _scrape_imdb_movie(url, timeout)
    if host == "themoviedb.org":
        access_token = os.environ.get("TMDB_API_READ_ACCESS_TOKEN", "").strip()
        if access_token:
            return _scrape_tmdb_api(url, access_token, timeout)

    r = SESSION.get(url, timeout=timeout)
    try:
        r.raise_for_status()
    except requests.HTTPError as exc:
        if (
            host == "themoviedb.org"
            and exc.response is not None
            and exc.response.status_code == 403
        ):
            raise requests.HTTPError(
                "TMDB blocked the metadata request (HTTP 403). "
                "Set TMDB_API_READ_ACCESS_TOKEN or use another metadata URL.",
                request=exc.request,
                response=exc.response,
            ) from exc
        raise

    soup = BeautifulSoup(r.text, "html.parser")

    # Adult Film Database has a distinct, structured page format.  Dispatch
    # only for that host so the existing Rarelust parsing remains unchanged.
    if host == "adultfilmdatabase.com":
        return _scrape_adultfilmdatabase_movie(url, soup)
    if host == "themoviedb.org":
        return _scrape_tmdb_movie(url, soup)

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


def _scrape_imdb_movie(url, timeout):
    """Fetch title metadata from IMDb's public GraphQL endpoint."""
    title_match = re.search(r"/title/(tt\d+)", urlparse(url).path, re.IGNORECASE)
    if not title_match:
        raise ValueError("IMDb URL must contain a title ID, such as tt0160208.")

    query = """
        query GetTitle($id: ID!) {
            title(id: $id) {
                titleText { text }
                releaseYear { year }
                titleType { text id }
                genres { genres { text } }
                plot { plotText { plainText } }
                primaryImage { url }
            }
        }
    """
    response = SESSION.post(
        "https://api.graphql.imdb.com/",
        json={"query": query, "variables": {"id": title_match.group(1)}},
        headers={
            "Origin": "https://www.imdb.com",
            "Referer": "https://www.imdb.com/",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ValueError(payload["errors"][0].get("message", "IMDb lookup failed."))

    title = payload.get("data", {}).get("title")
    if not title:
        raise ValueError(f"IMDb title {title_match.group(1)} was not found.")

    genre_data = (title.get("genres") or {}).get("genres") or []
    genres = [
        item.get("text", "").strip()
        for item in genre_data
        if isinstance(item, dict) and item.get("text", "").strip()
    ]
    title_type = title.get("titleType") or {}
    if isinstance(title_type, dict) and (
        str(title_type.get("text", "")).strip().casefold() == "short"
        or str(title_type.get("id", "")).strip().casefold() == "short"
    ):
        if not any(genre.casefold() == "short" for genre in genres):
            genres.append("Short")
    plot = (title.get("plot") or {}).get("plotText") or {}
    plot = plot.get("plainText", "")
    primary_image = title.get("primaryImage") or {}
    image_url = primary_image.get("url")

    return {
        "name": (title.get("titleText") or {}).get("text", ""),
        "year": str((title.get("releaseYear") or {}).get("year") or ""),
        "category": ", ".join(dict.fromkeys(genres)),
        "description": plot.strip() if isinstance(plot, str) else "",
        "images": [image_url] if image_url else [],
        "size_text": "",
        "size_bytes": None,
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


def _scrape_tmdb_api(url, access_token, timeout):
    """Fetch TMDB metadata through its official API using a local access token."""
    movie_match = re.search(r"/movie/(\d+)(?:[-/]|$)", urlparse(url).path)
    if not movie_match:
        raise ValueError("TMDB movie URL must contain a numeric movie ID.")

    movie_id = movie_match.group(1)
    response = SESSION.get(
        f"https://api.themoviedb.org/3/movie/{movie_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"language": "en-US"},
        timeout=timeout,
    )
    if response.status_code in {401, 403}:
        raise ValueError(
            "TMDB API authorization failed. Check TMDB_API_READ_ACCESS_TOKEN."
        )
    if response.status_code == 404:
        raise ValueError(f"TMDB movie ID {movie_id} was not found.")
    response.raise_for_status()

    movie = response.json()
    if not isinstance(movie, dict):
        raise ValueError(f"TMDB returned invalid metadata for movie ID {movie_id}.")

    release_date = movie.get("release_date") or ""
    year_match = re.match(r"\d{4}", release_date)
    raw_genres = movie.get("genres")
    genres = []
    if isinstance(raw_genres, list):
        genres = [
            genre["name"].strip()
            for genre in raw_genres
            if isinstance(genre, dict)
            and isinstance(genre.get("name"), str)
            and genre["name"].strip()
        ]
    images = [
        f"https://image.tmdb.org/t/p/original{image_path}"
        for image_path in (movie.get("poster_path"), movie.get("backdrop_path"))
        if isinstance(image_path, str) and image_path.startswith("/")
    ]

    return {
        "name": movie.get("title") or movie.get("original_title") or "",
        "year": year_match.group(0) if year_match else "",
        "category": ", ".join(dict.fromkeys(genres)),
        "description": (movie.get("overview") or "").strip(),
        "images": images,
        "size_text": "",
        "size_bytes": None,
    }


def _scrape_tmdb_movie(url, soup):
    """Extract movie metadata and artwork from a TMDB movie page."""
    structured_data = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (TypeError, json.JSONDecodeError):
            continue

        candidates = payload if isinstance(payload, list) else [payload]
        if isinstance(payload, dict) and isinstance(payload.get("@graph"), list):
            candidates.extend(payload["@graph"])
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            types = candidate.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if "Movie" in types:
                structured_data = candidate
                break
        if structured_data:
            break

    def meta_content(selector):
        node = soup.select_one(selector)
        return node.get("content", "").strip() if node else ""

    title = (
        structured_data.get("name")
        or meta_content('meta[property="og:title"]')
        or (soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else "")
    )
    title_year = re.search(r"\b((?:19|20)\d{2})\)?\s*$", title)
    published_year = re.search(
        r"\b((?:19|20)\d{2})\b",
        str(structured_data.get("datePublished", "")),
    )
    release_node = soup.select_one(".release")
    release_year = re.search(
        r"\b((?:19|20)\d{2})\b",
        release_node.get_text(" ", strip=True) if release_node else "",
    )
    year_match = published_year or release_year or title_year
    year = year_match.group(1) if year_match else ""
    name = re.sub(r"\s*\(((?:19|20)\d{2})\)\s*$", "", title).strip()

    raw_genres = structured_data.get("genre") or []
    if isinstance(raw_genres, str):
        raw_genres = [raw_genres]
    genres = []
    for genre in raw_genres:
        if isinstance(genre, dict):
            genre = genre.get("name", "")
        genre = str(genre).strip()
        if genre and genre.casefold() not in {item.casefold() for item in genres}:
            genres.append(genre)
    if not genres:
        for link in soup.select('a[href*="/genre/"]'):
            genre = link.get_text(" ", strip=True)
            if genre and genre.casefold() not in {item.casefold() for item in genres}:
                genres.append(genre)
    category = ", ".join(genres)

    description = (
        structured_data.get("description")
        or meta_content('meta[property="og:description"]')
        or meta_content('meta[name="description"]')
    ).strip()
    if not description:
        overview = soup.select_one(".overview, [data-testid='overview']")
        description = overview.get_text(" ", strip=True) if overview else ""

    image_urls = []

    def add_image(source):
        if not source:
            return
        absolute_url = urljoin(url, source.strip())
        parsed = urlparse(absolute_url)
        if parsed.hostname not in {"media.themoviedb.org", "image.tmdb.org"}:
            return
        image_path = parsed.path
        if "/t/p/" in image_path:
            image_file = image_path.split("/t/p/", 1)[1].split("/", 1)
            if len(image_file) == 2:
                image_path = "/t/p/original/" + image_file[1]
        if not image_path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            return
        normalized_url = f"https://image.tmdb.org{image_path}"
        if normalized_url not in image_urls:
            image_urls.append(normalized_url)

    structured_images = structured_data.get("image") or []
    if isinstance(structured_images, str):
        structured_images = [structured_images]
    for image in structured_images:
        add_image(image.get("url") if isinstance(image, dict) else image)
    add_image(meta_content('meta[property="og:image"]'))

    for image in soup.find_all("img"):
        for attribute in ("data-src", "data-lazy-src", "src"):
            add_image(image.get(attribute))
        srcset = image.get("srcset", "")
        for candidate in srcset.split(","):
            add_image(candidate.strip().split(" ")[0])

    return {
        "name": name,
        "year": year,
        "category": category,
        "description": description,
        "images": image_urls[:2],
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

def _extract_rarelust_inline_listing(content, page_url, page_category=""):
    """Extract movie records embedded directly in a Rarelust long-form page.

    This handles pages such as /asian-movies/ where title/year, description,
    cover images and file details are published in the page body rather than
    on separate Rarelust movie-detail pages.
    """
    if content is None:
        return None

    blocks = []
    for node in content.find_all(["p", "h2", "h3", "li", "figure"], recursive=True):
        if node.find(["p", "h2", "h3", "li"], recursive=True):
            continue
        blocks.append(node)
    if not blocks:
        blocks = [node for node in content.find_all(recursive=False) if getattr(node, "name", None)]

    # A title is normally the first text on a line and has a year in brackets.
    title_pattern = re.compile(
        r"^\s*(.{2,160}?)\s*[\(\[]((?:18|19|20)\d{2})[\)\]](?:\s|$|[/|–—-])",
        re.S,
    )
    candidates = []
    for idx, node in enumerate(blocks):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        match = title_pattern.match(text)
        if not match:
            continue
        name = match.group(1).strip(" -–—|/")
        if name.lower().startswith(("description", "starring", "cover", "preview", "download")):
            continue
        # The alternate/original title is commonly written after a slash.
        name = re.split(r"\s*/\s*", name, maxsplit=1)[0].strip()
        if name:
            candidates.append((idx, name, match.group(2)))

    # A normal archive has one title per post, not many records in one body.
    if len(candidates) < 2:
        return None

    entries = []
    for pos, (start_idx, name, year) in enumerate(candidates):
        end_idx = candidates[pos + 1][0] if pos + 1 < len(candidates) else len(blocks)
        group = blocks[start_idx:end_idx]
        text_parts = [re.sub(r"\s+", " ", n.get_text(" ", strip=True)).strip() for n in group]
        combined = " ".join(t for t in text_parts if t)
        desc_match = re.search(
            r"Description\s*:\s*(.*?)(?=\s+(?:cover|preview|dvdrip|webrip|vhsrip|brrip|bluray|download)\b|$)",
            combined,
            re.I,
        )
        description = desc_match.group(1).strip(" .:-") if desc_match else ""
        image_urls = []
        for node in group:
            image_nodes = [node] if getattr(node, "name", None) == "img" else node.find_all("img")
            for img in image_nodes:
                src = img.get("data-src") or img.get("data-lazy-src") or img.get("data-original") or img.get("src")
                if not src:
                    continue
                image_url = urljoin(page_url, src)
                image_path = urlparse(image_url).path.lower()
                if not image_path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    continue
                if any(x in image_path for x in ("logo", "avatar", "icon", "banner", "smilies")):
                    continue
                if image_url not in image_urls:
                    image_urls.append(image_url)
        size_info = extract_size_info(combined)
        entries.append({
            "name": name,
            "year": year,
            "category": page_category,
            "url": page_url,
            "size_text": size_info.get("size_text") or "",
            "size_bytes": size_info.get("size_bytes"),
            "description": description,
            "cover1_url": image_urls[0] if image_urls else "",
            "cover2_url": image_urls[1] if len(image_urls) > 1 else "",
            "images": image_urls[:2],
            "_inline_listing": True,
        })
    return entries


def scrape_category_entries(category_url, timeout=15, max_pages=100):
    """Read a Rarelust page using the appropriate layout strategy.

    Inline listing pages are parsed directly. Conventional WordPress category
    archives return individual movie permalinks for the existing detail scraper.
    Archive pagination is followed, with same-host checks and deduplication.
    """
    start_url = (category_url or "").strip()
    if not start_url:
        return []
    start_host = (urlparse(start_url).hostname or "").lower()
    pending = [start_url]
    visited = set()
    seen = set()
    entries = []

    while pending and len(visited) < max(1, int(max_pages)):
        page_url = pending.pop(0).split("#", 1)[0].rstrip("/")
        if page_url in visited:
            continue
        visited.add(page_url)
        response = SESSION.get(page_url, timeout=timeout)
        response.raise_for_status()
        actual_url = getattr(response, "url", None) or page_url
        soup = BeautifulSoup(response.text, "html.parser")
        content = soup.select_one(".entry-content, .post-content, main .content, main")
        category_parts = []
        for node in soup.select(".entry-meta a[rel='category tag'], .entry-meta a, .cat-links a"):
            label = node.get_text(" ", strip=True)
            if label and label.lower() not in {x.lower() for x in category_parts}:
                category_parts.append(label)
        page_category = ", ".join(category_parts)

        inline_entries = _extract_rarelust_inline_listing(content, actual_url, page_category)
        if inline_entries:
            for entry in inline_entries:
                key = (re.sub(r"[^a-z0-9]", "", entry["name"].lower()), entry["year"])
                if key[0] and key not in seen:
                    seen.add(key)
                    entries.append(entry)
        else:
            # Conventional archive: collect same-site movie detail permalinks.
            for anchor in soup.find_all("a", href=True):
                href = urljoin(actual_url, anchor["href"]).split("#", 1)[0].rstrip("/")
                parsed = urlparse(href)
                if (parsed.hostname or "").lower() != start_host:
                    continue
                if not re.match(r"^/.+-\d{4}/?$", parsed.path):
                    continue
                key = href.lower()
                if key not in seen:
                    seen.add(key)
                    entries.append({"url": href, "_inline_listing": False})

        next_link = soup.select_one(
            "a[rel='next'], .nav-links a.next, .pagination a.next, "
            "a.next.page-numbers, a.page-numbers.next"
        )
        if next_link and next_link.get("href"):
            next_url = urljoin(actual_url, next_link["href"]).split("#", 1)[0].rstrip("/")
            parsed_next = urlparse(next_url)
            if ((parsed_next.hostname or "").lower() == start_host
                    and next_url not in visited and next_url not in pending):
                pending.append(next_url)
    return entries


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

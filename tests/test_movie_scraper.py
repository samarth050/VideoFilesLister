import unittest
from unittest.mock import patch

from utils.movie_scraper import extract_size_info, scrape_movie


class MovieScraperSizeTests(unittest.TestCase):
    def test_extract_size_info_from_gb_text(self):
        info = extract_size_info("Download Size: 1.5 GB")
        self.assertEqual(info["size_text"], "1.5 GB")
        self.assertEqual(info["size_bytes"], int(1.5 * 1024**3))

    def test_extract_size_info_from_mb_text(self):
        info = extract_size_info("File size 512 MB")
        self.assertEqual(info["size_text"], "512 MB")
        self.assertEqual(info["size_bytes"], int(512 * 1024**2))

    def test_extract_size_info_from_rarelust_metadata_line(self):
        info = extract_size_info("2.26GB | 54:56mins | 720×480 | mkv | English")
        self.assertEqual(info["size_text"], "2.26 GB")
        self.assertEqual(info["size_bytes"], int(2.26 * 1024**3))


class MovieScraperCategoryTests(unittest.TestCase):
    class _Response:
        def __init__(self, html):
            self.text = html

        def raise_for_status(self):
            pass

    def _scrape_category(self, category):
        html = f"""
            <html><body>
                <div class="entry-meta">Categories: {category}</div>
                <div class="entry-content"><p>Description: Test record.</p></div>
            </body></html>
        """
        with patch("utils.movie_scraper.SESSION.get", return_value=self._Response(html)):
            return scrape_movie("https://example.test/test-title-2020")

    def test_scrape_movie_classifies_fantasy(self):
        self.assertEqual(self._scrape_category("Fantasy")["category"], "Fantasy")

    def test_scrape_movie_classifies_documentary(self):
        self.assertEqual(
            self._scrape_category("Documentary")["category"], "Documentary"
        )

    def test_scrape_adult_film_database_video_page(self):
        html = """
            <html><body>
                <h1 class="w3-xxlarge" itemprop="name">
                    Private Black Label 30 - Scottish Loveknot
                </h1>
                <div>Studio: Private (2003)</div>
                <article><p itemprop="description">
                    A complete description from the video record.
                </p></article>
                <div class="w3-white">
                    <div class="w3-theme-l4 w3-padding">Genres</div>
                    <div class="w3-container"><p>
                        <a href="/browse.cfm?cf=European"><span>European</span></a>
                        <a href="/browse.cfm?cf=Feature"><span>Feature</span></a>
                    </p></div>
                </div>
                <img src="/Graphics/Boxes/200/Front/67376.jpg">
                <img src="/Graphics/Boxes/200/Back/67376.jpg">
                <img src="/Graphics/PornStars/example_1.jpg">
            </body></html>
        """
        with patch("utils.movie_scraper.SESSION.get", return_value=self._Response(html)):
            data = scrape_movie(
                "https://www.adultfilmdatabase.com/video/67376/private-black-label-30/"
            )

        self.assertEqual(data["name"], "Private Black Label 30 - Scottish Loveknot")
        self.assertEqual(data["year"], "2003")
        self.assertEqual(data["category"], "European, Feature")
        self.assertEqual(data["description"], "A complete description from the video record.")
        self.assertEqual(data["images"], [
            "https://www.adultfilmdatabase.com/Graphics/Boxes/200/Front/67376.jpg",
            "https://www.adultfilmdatabase.com/Graphics/Boxes/200/Back/67376.jpg",
        ])


if __name__ == "__main__":
    unittest.main()

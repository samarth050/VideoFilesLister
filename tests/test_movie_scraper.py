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


if __name__ == "__main__":
    unittest.main()

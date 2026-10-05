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

    def test_scrape_movie_classifies_short(self):
        self.assertEqual(self._scrape_category("Short")["category"], "Short")

    def test_scrape_movie_classifies_animation(self):
        self.assertEqual(self._scrape_category("Animation")["category"], "Animation")

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

    def test_scrape_tmdb_movie_page(self):
        html = """
            <html><head>
                <meta property="og:title" content="Femmes (1983)">
                <meta property="og:description" content="A film overview.">
                <meta property="og:image" content="https://media.themoviedb.org/t/p/w300_and_h450_face/poster.jpg">
                <script type="application/ld+json">
                    {"@type":"Movie","name":"Femmes"}
                </script>
            </head><body>
                <span class="release">06/22/1983 (FR)</span>
                <a href="/genre/18-drama/movie">Drama</a>
                <img src="https://media.themoviedb.org/t/p/w533_and_h300_face/backdrop.jpg">
            </body></html>
        """
        with patch("utils.movie_scraper.SESSION.get", return_value=self._Response(html)):
            data = scrape_movie("https://www.themoviedb.org/movie/336543-femmes")

        self.assertEqual(data["name"], "Femmes")
        self.assertEqual(data["year"], "1983")
        self.assertEqual(data["category"], "Drama")
        self.assertEqual(data["description"], "A film overview.")
        self.assertEqual(data["images"], [
            "https://image.tmdb.org/t/p/original/poster.jpg",
            "https://image.tmdb.org/t/p/original/backdrop.jpg",
        ])

    def test_scrape_imdb_title_page(self):
        class GraphQLResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": {
                        "title": {
                            "titleText": {"text": "Dirty Woman"},
                            "releaseYear": {"year": 1989},
                            "genres": {"genres": [{"text": "Adult"}, {"text": "Drama"}]},
                            "plot": {"plotText": {"plainText": "Dirty Woman."}},
                            "primaryImage": None,
                        }
                    }
                }

        with patch("utils.movie_scraper.SESSION.post", return_value=GraphQLResponse()) as post:
            with patch("utils.movie_scraper.SESSION.get") as get:
                data = scrape_movie("https://www.imdb.com/title/tt0160208")

        self.assertEqual(data["name"], "Dirty Woman")
        self.assertEqual(data["year"], "1989")
        self.assertEqual(data["category"], "Adult, Drama")
        self.assertEqual(data["description"], "Dirty Woman.")
        self.assertEqual(data["images"], [])
        post.assert_called_once()
        get.assert_not_called()
        self.assertEqual(post.call_args.kwargs["json"]["variables"]["id"], "tt0160208")

    def test_scrape_imdb_short_title_type(self):
        class GraphQLResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": {
                        "title": {
                            "titleText": {"text": "Animated Short"},
                            "titleType": {"text": "Short", "id": "short"},
                            "genres": {"genres": [{"text": "Animation"}]},
                        }
                    }
                }

        with patch("utils.movie_scraper.SESSION.post", return_value=GraphQLResponse()):
            data = scrape_movie("https://www.imdb.com/title/tt1234567")

        self.assertEqual(data["category"], "Animation, Short")


if __name__ == "__main__":
    unittest.main()

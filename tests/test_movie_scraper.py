import unittest

from utils.movie_scraper import extract_size_info


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


if __name__ == "__main__":
    unittest.main()

# Rarelust dual-layout scraper update

## Source baseline

This update was made from the user-provided `LatestCode(2).zip` source archive. It does not use the previously generated Rarelust category/connection-fix ZIP as its baseline.

## Page layouts handled

- **Inline listing pages**, such as `https://rarelust.com/asian-movies/`: reads multiple movie entries directly from the page body, including title, year, description, available cover/preview image URLs, and file-size text. It does not require a Rarelust detail URL for each movie.
- **Conventional archive pages**, such as `https://rarelust.com/category/asian-classic-erotica-movies/page/2/`: extracts same-site movie detail links and lets the existing `scrape_movie()` workflow retrieve the metadata from each linked page.

The archive scraper follows recognized same-host WordPress pagination links with a 100-page safety limit and deduplicates entries. The To Download workflow compares inline-listing entries by normalized movie title/year (not the shared archive URL), preventing entries from the same long page from being incorrectly treated as duplicates.

## Notes

- Existing To Download UI continues to show movies missing from the database, consistent with the supplied baseline UI.
- The direct-list parser extracts descriptions and cover image URLs for each entry, but this update does not download those images or save descriptions into `MovieDetails`.
- A `Connection aborted` error is a separate transport/network failure; this update addresses page structure and does not include the prior connection-retry enhancement.

## Verification

- Python compilation completed successfully.
- Automated test suite: 30 passed.

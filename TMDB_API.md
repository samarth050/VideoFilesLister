# TMDB metadata lookup

The movie metadata fetcher can use TMDB's official API instead of scraping its
website. Set the `TMDB_API_READ_ACCESS_TOKEN` environment variable to your
TMDB API Read Access Token before starting FileLister. The token is sent as a
Bearer token and is not saved in the project or application settings.

In PowerShell, for a single launch:

```powershell
$env:TMDB_API_READ_ACCESS_TOKEN = "your-token"
python FileLister.py
```

For the packaged executable, set the environment variable in PowerShell before
launching it:

```powershell
$env:TMDB_API_READ_ACCESS_TOKEN = "your-token"
.\FileLister.exe
```

TMDB provides the token in the API settings of your account. See
[TMDB application authentication](https://developer.themoviedb.org/docs/authentication-application).

Without this variable, FileLister continues to use the TMDB page scraper. If
TMDB denies that request with HTTP 403, use the official API token or provide a
metadata URL from another supported source.

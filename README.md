# Video Files Lister

## TMDB metadata for the packaged app

To fetch movie metadata from TMDB in the packaged Windows app, configure your
own TMDB API Read Access Token. Each user must obtain and configure their own
token; do not share or publish it.

For a one-time launch, open PowerShell in the folder containing `FileLister.exe`
and run:

```powershell
$env:TMDB_API_READ_ACCESS_TOKEN = "your-TMDB-Read-Access-Token"
.\FileLister.exe
```

To make the token available for future launches, add a user environment
variable named `TMDB_API_READ_ACCESS_TOKEN` in Windows:

1. Search Windows for **Edit environment variables for your account**.
2. Under **User variables**, choose **New**.
3. Enter `TMDB_API_READ_ACCESS_TOKEN` as the variable name and your token as
   the value.
4. Save the change, then restart FileLister.

The app reads the token when it starts and sends it to TMDB's official API.
It is not stored in the app's settings. For more details, see
[TMDB_API.md](./TMDB_API.md).

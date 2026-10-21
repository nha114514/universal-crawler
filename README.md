# Universal Crawler

**开发中 · 仅限个人学习研究 · 非商用**

**In development · Personal study and research only · Noncommercial**

A Windows desktop application and MCP server for finding webpage, paper/PDF,
video, audio, and attachment resources. Paste URLs or enter keywords, inspect
the discovered entries, and choose what to download. Both interfaces share a
local library. This is an experimental development snapshot, not a stable release.

## Features

- URL discovery with static requests and Edge rendering for dynamic pages.
- Keyword discovery across domestic, foreign, or all sources, classified by website.
- Google/Bing result parsing, query expansion, deduplication, and local link ranking;
  these are client-side components, not the search engines' private ranking systems.
- Dedicated persistent Edge login sessions, without browser extensions or OpenCLI.
- Selected and batch downloads, configurable storage, and file-size limit `0` for unlimited.
- Direct Python-library integration with Scrapling, Playwright, yt-dlp, and pypdf.

Full text, PDF text, and subtitles are not extracted automatically in the resource
workflow. A video entry or cover image is not proof that its original can be downloaded.

## Requirements

Windows, Python **3.12**, Microsoft Edge, and FFmpeg/FFprobe on `PATH` for media
merging and validation. Node.js is needed for the frontend syntax verification step.
Python dependencies are pinned in `requirements.lock.txt`; no virtual environment,
browser profile, or browser extension is distributed with the source.

## Start locally

From the project directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\Start-Crawler.cmd
```

`build/Setup.ps1` is an optional **offline** helper requiring explicit paths to a
Python 3.12 executable, an existing wheelhouse, and browser resources. The published
repository does not include these local archives. If dependencies or browser
resources are unavailable, installation must be completed before starting the app.

1. Paste one or more URLs and select **Scan Resources**.
2. Review the entries and attachments; select individual items or a batch.
3. Select **Download Selected** and inspect the task log for results and errors.
4. For an authenticated site, open **Dedicated Edge for Login** in settings,
   log in manually, and keep that window open. The crawler reuses its own session.

The EXE launcher can be built with `build/Build.ps1`. It requires the project source
and local `.venv` beside it; it is not a standalone bundled application.

## MCP

Configure an MCP client to run the project's `.venv\Scripts\python.exe` with the
absolute path to `run_mcp.py` as its argument. For a TOML client, for example:

```toml
[mcp_servers.crawler-system]
command = 'D:\path\to\universal-crawler\.venv\Scripts\python.exe'
args = ['D:\path\to\universal-crawler\run_mcp.py']

[mcp_servers.crawler-system.env]
PYTHONUTF8 = "1"
```

Tools include `collect`, `job_status`, `download_batch`, and `open_login_browser`.
Downloads start only when selected. Client configuration and credentials stay local.

## Storage and privacy

Runtime files default to the project's `data/` directory. The resource storage
location is configurable in settings; the shared index stays at `data/library.sqlite3`.
Browser profiles, sessions, API tokens, downloaded files, exports, and databases
are excluded from version control. Never add your `data/` or `.venv/` directories.

## Development status and limitations

Site layouts, authentication, rate limiting, and formats can change. Google
verification/rate limiting and some dynamic pages remain unreliable. iQIYI testing
previously obtained one full low-resolution file using an authorized member session;
4K downloading has not been verified. Netflix member full-length downloading has
not been verified. Protected or unsupported media is reported as a failure; the
project does not provide DRM decryption or membership bypasses.

Run `build/Verify.ps1` for the production regression suite, dependency checks, and
frontend syntax check. See [Development notes](docs/DEVELOPMENT.md) for the scope
of these checks. Passing local tests does not establish support for every website.

## License

Original project code is available under the [Personal Noncommercial Research
License](LICENSE). **Commercial use requires separate permission.** Third-party
components retain their own licenses; see [NOTICES.md](NOTICES.md).

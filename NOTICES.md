# Notices and Third-Party Licenses

Universal Crawler is under active development for personal, noncommercial study
and research. Original project code is covered by the repository's `LICENSE`.
That license does not replace third-party licenses or grant rights to website content.

## Runtime dependencies

Dependencies are installed separately rather than vendored into this repository.
Pinned versions are recorded in `requirements.lock.txt`. Consult each installed
distribution's metadata, notices, and license files for authoritative terms.

| Component | Pinned version | License index |
| --- | --- | --- |
| Scrapling | 0.4.15 | BSD-3-Clause |
| yt-dlp | 2026.8.19 | Unlicense; bundled components may differ |
| pypdf | 6.19.0 | BSD-3-Clause |
| FastAPI | 0.110.2 | MIT |
| MCP Python SDK | 2.3.0 | MIT |
| Playwright | 1.63.0 | Apache-2.0 |
| PyInstaller | 6.22.3 | GPLv2-or-later with bootloader distribution exception |

This is an index, not a replacement for the full licenses, notices, or terms of
indirect dependencies, browser drivers, browsers, or FFmpeg builds. No browser,
FFmpeg build, virtual environment, or dependency wheelhouse is included here.

## Source provenance

The in-tree `crawler_system/search_logic/` package was integrated from an earlier
local search reconstruction project. It contains observable result-page parsers,
query expansion, deduplication, and local ranking code. It does not contain the
private server-side source code or ranking algorithms of Google, Bing, or Bilibili.

MediaCrawler was evaluated in the earlier local source archive and uses the
NON-COMMERCIAL LEARNING LICENSE 1.1. MediaCrawler and its source archive are not
bundled or loaded by this extension-free runtime; integrating them separately
requires retaining and following their own license and attribution terms.

The background lifetime implementation references Microsoft's Explorer automation
guidance: <https://devblogs.microsoft.com/oldnewthing/20131118-00/?p=2643/>.

Historical handoff prompts, raw diagnostic evidence, screenshots, local account
data, and the private development history are not part of the GitHub source snapshot.
Existing third-party names and client configuration paths are kept where required
for correct attribution or interoperability.

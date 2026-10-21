import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import httpx
import win32api
import win32con
import win32job
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.os.win32.utilities import _process_jobs
from crawler_system.config import DATA, python_command, runtime_env
from crawler_system.service import alive


async def main():
    params = StdioServerParameters(command=python_command(), args=[str(ROOT / "run_mcp.py")], env=runtime_env())
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
            await session.initialize()
            await session.call_tool("tool_status", {})
            lease = json.loads((DATA / "service.json").read_text())
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, lease["pid"])
            try:
                members = [{"mcp_pid": process.pid, "api_pid": lease["pid"], "api_in_client_job": bool(win32job.IsProcessInJob(handle, job))} for process, job in _process_jobs.items()]
                print(members, flush=True)
            finally:
                handle.Close()
    await asyncio.sleep(1)
    survived = alive(lease["url"])
    print({"api_survives_mcp_exit": survived}, flush=True)
    (ROOT / 'evidence/service-lifetime.json').write_text(json.dumps({"checks": members, "api_survives_mcp_exit": survived}, indent=2), encoding='utf-8')
    assert survived


if __name__ == "__main__":
    asyncio.run(main())

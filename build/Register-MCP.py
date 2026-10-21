"""Register only this MCP entry, preserving all other TOML semantics."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import tomllib


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path.home() / ".codex/config.toml"))
    parser.add_argument("--restore-source")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    path = Path(args.config)
    original = Path(args.restore_source).read_text(encoding="utf-8") if args.restore_source else path.read_text(encoding="utf-8")
    old = tomllib.loads(original)
    output, dropping = [], False
    for line in original.splitlines(keepends=True):
        if re.match(r"^\s*\[", line):
            dropping = bool(re.match(r"^\s*\[mcp_servers\.crawler-system(?:\.[^\]]+)?\]\s*$", line.strip()))
        if not dropping:
            output.append(line)
    entry = {"command": str(root / ".venv/Scripts/python.exe"), "args": [str(root / "run_mcp.py")], "startup_timeout_sec": 60, "tool_timeout_sec": 180, "env": {"PYTHONUTF8": "1"}}
    block = "\n[mcp_servers.crawler-system]\ncommand = " + json.dumps(entry["command"]) + "\nargs = " + json.dumps(entry["args"]) + "\nstartup_timeout_sec = 60\ntool_timeout_sec = 180\n\n[mcp_servers.crawler-system.env]\nPYTHONUTF8 = " + json.dumps("1") + "\n"
    changed = "".join(output).rstrip() + "\n" + block
    parsed = tomllib.loads(changed)
    old.get("mcp_servers", {}).pop("crawler-system", None)
    compared = dict(parsed)
    compared["mcp_servers"] = {key: value for key, value in parsed.get("mcp_servers", {}).items() if key != "crawler-system"}
    assert old == compared, "Other configuration entries would change"
    directory = root / "data/backups"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, directory / ("mcp-config-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".toml"))
    path.write_text(changed, encoding="utf-8")
    evidence = root / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "mcp-registration.json").write_text(json.dumps({"server": "crawler-system", "entry": entry, "other_settings_preserved": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("MCP registered; existing servers and model settings preserved.")


if __name__ == "__main__":
    main()

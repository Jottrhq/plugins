#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def source_key(source):
    if isinstance(source, str):
        return source.lower()
    if isinstance(source, dict):
        return (source.get("repo") or source.get("repository") or source.get("name") or "").lower()
    return ""


def main():
    parser = argparse.ArgumentParser(description="Add a plugin repository to plugin-sources.json.")
    parser.add_argument("plugin", help="Repository name, owner/repo, or GitHub URL")
    parser.add_argument("--sources", default="plugin-sources.json")
    args = parser.parse_args()
    path = Path(args.sources)
    data = json.loads(path.read_text(encoding="utf-8"))
    plugins = data.setdefault("plugins", [])
    wanted = args.plugin.strip()
    if not wanted:
        raise SystemExit("plugin must not be empty")
    wanted_key = wanted.lower()
    if any(source_key(item) == wanted_key for item in plugins):
        print(f"{wanted} already exists in {path}")
        return 0
    plugins.append(wanted)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Added {wanted} to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

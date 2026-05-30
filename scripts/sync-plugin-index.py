#!/usr/bin/env python3
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

SEMVER_RE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?")


def fail(message):
    raise RuntimeError(message)


def http_error_message(url, exc, context):
    hint = ""
    if exc.code == 404:
        hint = " Check the repository exists, has a published release, and the token can read it."
    elif exc.code in {401, 403}:
        hint = " Check the token permissions for this repository."
    return f"{context}: HTTP {exc.code} for {url}.{hint}"


def request_json(url, token=None, context="GitHub API request"):
    request = urllib.request.Request(url, headers=api_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        fail(http_error_message(url, exc, context))


def request_bytes(url, token=None, context="download"):
    request = urllib.request.Request(url, headers=api_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        fail(http_error_message(url, exc, context))


def api_headers(token=None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "jottr-plugin-index-sync",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_repo(source, default_owner):
    if isinstance(source, str):
        repo = source
        options = {}
    elif isinstance(source, dict):
        repo = source.get("repo") or source.get("repository") or source.get("name")
        options = dict(source)
    else:
        fail(f"plugin source must be string or object, got {type(source).__name__}")
    if not repo:
        fail("plugin source is missing repo")
    if repo.startswith("https://github.com/"):
        parsed = urlparse(repo)
        parts = parsed.path.strip("/").split("/")
        if len(parts) < 2:
            fail(f"invalid GitHub repository URL: {repo}")
        owner, name = parts[0], parts[1]
    elif "/" in repo:
        owner, name = repo.split("/", 1)
    else:
        owner, name = default_owner, repo
    if not owner:
        fail(f"missing owner for plugin source: {repo}")
    return owner, name, options


def github_repo_url(owner, repo):
    return f"https://github.com/{owner}/{repo}"


def get_releases(owner, repo, token):
    url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"
    releases = request_json(url, token, f"{owner}/{repo}: fetch releases")
    if not isinstance(releases, list):
        fail(f"{owner}/{repo}: releases response must be a list")
    releases = [release for release in releases if not release.get("draft")]
    if not releases:
        fail(f"{owner}/{repo}: no published releases found")
    return releases


def get_manifest(owner, repo, tag_name, token):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/plugin.json?ref={tag_name}"
    request = urllib.request.Request(url, headers={**api_headers(token), "Accept": "application/vnd.github.raw+json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        fail(http_error_message(url, exc, f"{owner}/{repo}@{tag_name}: fetch plugin.json"))


def get_asset(release, asset_name):
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            return asset
    return None


def release_zip_assets(release, plugin_id):
    pattern = re.compile(rf"^{re.escape(plugin_id)}-({SEMVER_RE.pattern})\.zip$")
    matches = []
    for asset in release.get("assets", []):
        name = asset.get("name", "")
        match = pattern.match(name)
        if match:
            matches.append((match.group(1), asset))
    return matches


def version_from_tag(tag_name):
    matches = SEMVER_RE.findall(tag_name or "")
    if not matches:
        return None
    match = SEMVER_RE.search(tag_name)
    return match.group(0) if match else None


def semver_key(version):
    match = SEMVER_RE.fullmatch(version or "")
    if not match:
        return (-1, -1, -1, version or "")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), version)


def verify_assets(plugin_id, version, zip_asset, checksum_asset, token):
    checksum_text = request_bytes(
        checksum_asset["browser_download_url"],
        token,
        f"{plugin_id} {version}: download checksum asset",
    ).decode("utf-8").strip()
    expected = checksum_text.split()[0] if checksum_text else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        fail(f"{plugin_id} {version}: checksum asset does not start with a SHA-256 digest")
    zip_name = zip_asset["name"]
    if zip_name not in checksum_text:
        fail(f"{plugin_id} {version}: checksum asset does not reference {zip_name}")
    zip_data = request_bytes(
        zip_asset["browser_download_url"],
        token,
        f"{plugin_id} {version}: download zip asset",
    )
    actual = sha256_bytes(zip_data)
    if actual.lower() != expected.lower():
        fail(f"{plugin_id} {version}: zip checksum mismatch: expected {expected}, got {actual}")


def build_version_entries(owner, repo, release, plugin_id, token, verify_downloads):
    tag_name = release.get("tag_name") or "untagged-release"
    entries = []
    for version, zip_asset in release_zip_assets(release, plugin_id):
        checksum_name = f"{zip_asset['name']}.sha256"
        checksum_asset = get_asset(release, checksum_name)
        if not checksum_asset:
            print(
                f"Skipping {owner}/{repo}@{tag_name}: missing release asset {checksum_name}",
                file=sys.stderr,
            )
            continue
        if verify_downloads:
            verify_assets(plugin_id, version, zip_asset, checksum_asset, token)
        entries.append({
            "version": version,
            "package": {
                "downloadUrl": zip_asset["browser_download_url"],
                "checksumUrl": checksum_asset["browser_download_url"],
            },
            "source": {"type": "archive"},
        })
    if not entries:
        available = ", ".join(asset.get("name", "") for asset in release.get("assets", []))
        print(
            f"Skipping {owner}/{repo}@{tag_name}: no {plugin_id}-<version>.zip asset found; available: {available}",
            file=sys.stderr,
        )
    return entries


def build_plugin_entry(source, default_owner, token, verify_downloads):
    owner, repo, options = normalize_repo(source, default_owner)
    releases = get_releases(owner, repo, token)
    latest_release = releases[0]
    latest_tag = latest_release.get("tag_name")
    if not latest_tag:
        fail(f"{owner}/{repo}: latest release has no tag_name")
    manifest = get_manifest(owner, repo, latest_tag, token)
    plugin_id = manifest.get("name")
    if not plugin_id:
        fail(f"{owner}/{repo}@{latest_tag}: plugin.json is missing name")
    versions_by_number = {}
    for release in releases:
        for entry in build_version_entries(owner, repo, release, plugin_id, token, verify_downloads):
            versions_by_number[entry["version"]] = entry
    if not versions_by_number:
        fail(f"{owner}/{repo}: no published release has a valid {plugin_id}-<version>.zip and checksum asset pair")
    versions = sorted(versions_by_number.values(), key=lambda item: semver_key(item["version"]), reverse=True)
    latest_version = versions[0]["version"]
    description = options.get("description") or manifest.get("description", "")
    display_name = options.get("displayName") or manifest.get("displayName") or plugin_id
    return {
        "id": plugin_id,
        "displayName": display_name,
        "description": description,
        "repository": github_repo_url(owner, repo),
        "latestVersion": latest_version,
        "defaultEnabled": bool(options.get("defaultEnabled", True)),
        "versions": versions,
    }


def update_index_checksum(index_path, checksum_path):
    digest = sha256_file(index_path)
    Path(checksum_path).write_text(f"{digest}  {Path(index_path).name}\n", encoding="utf-8")
    return digest


def main():
    parser = argparse.ArgumentParser(description="Sync plugins.json from plugin repository releases.")
    parser.add_argument("--sources", default="plugin-sources.json")
    parser.add_argument("--output", default="plugins.json")
    parser.add_argument("--checksum", default="plugins.json.sha256")
    parser.add_argument("--no-verify-downloads", action="store_true", help="Skip downloading zip assets to verify checksum files.")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    args = parser.parse_args()

    sources = load_json(args.sources)
    if sources.get("schemaVersion") != 1:
        fail("plugin-sources.json schemaVersion must be 1")
    default_owner = sources.get("defaultOwner", "")
    plugins = sources.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        fail("plugin-sources.json plugins must be a non-empty list")

    entries = [
        build_plugin_entry(source, default_owner, args.token, not args.no_verify_downloads)
        for source in plugins
    ]
    entries.sort(key=lambda item: item["id"])

    previous = None
    output_path = Path(args.output)
    if output_path.exists():
        try:
            previous = load_json(output_path)
        except Exception:
            previous = None
    updated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if previous and previous.get("plugins") == entries:
        updated_at = previous.get("updatedAt", updated_at)
    index = {
        "schemaVersion": 1,
        "updatedAt": updated_at,
        "checksumAlgorithm": "sha256",
        "plugins": entries,
    }
    write_json(args.output, index)
    digest = update_index_checksum(args.output, args.checksum)
    print(f"Wrote {args.output}")
    print(f"Wrote {args.checksum}: {digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"sync-plugin-index failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

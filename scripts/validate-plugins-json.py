#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STRICT_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")
FORBIDDEN_MANIFEST_FIELDS = {"entry", "permissions", "contributes"}


def fail(errors, path, message):
    errors.append(f"{path}: {message}")


def expect_type(errors, data, key, expected, path, required=True):
    if key not in data:
        if required:
            fail(errors, path, f"missing required field '{key}'")
        return None
    value = data[key]
    if not isinstance(value, expected):
        name = expected.__name__ if hasattr(expected, "__name__") else str(expected)
        fail(errors, path, f"field '{key}' must be {name}")
        return None
    return value


def valid_url(value):
    parsed = urlparse(value)
    return parsed.scheme in {"https", "http", "file"} and bool(parsed.netloc or parsed.scheme == "file")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_checksum_file(index_path, checksum_path, errors):
    if not checksum_path.exists():
        fail(errors, checksum_path, "checksum file does not exist")
        return
    text = checksum_path.read_text(encoding="utf-8").strip()
    expected = text.split()[0] if text else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        fail(errors, checksum_path, "checksum must start with a 64-character SHA-256 hex digest")
        return
    actual = sha256_file(index_path)
    if actual.lower() != expected.lower():
        fail(errors, checksum_path, f"checksum mismatch: expected {expected}, got {actual}")


def validate_index(path, checksum_path=None):
    errors = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"{path}: invalid JSON: {exc}"]
    if not isinstance(data, dict):
        return [f"{path}: root must be an object"]

    schema_version = expect_type(errors, data, "schemaVersion", int, path)
    if schema_version != 1:
        fail(errors, path, "schemaVersion must be 1")
    checksum_algorithm = expect_type(errors, data, "checksumAlgorithm", str, path)
    if checksum_algorithm != "sha256":
        fail(errors, path, "checksumAlgorithm must be 'sha256'")
    plugins = expect_type(errors, data, "plugins", list, path)
    if isinstance(plugins, list):
        seen_plugins = set()
        for index, plugin in enumerate(plugins):
            plugin_path = f"{path}: plugins[{index}]"
            if not isinstance(plugin, dict):
                fail(errors, plugin_path, "must be an object")
                continue
            forbidden = FORBIDDEN_MANIFEST_FIELDS & set(plugin)
            if forbidden:
                fail(errors, plugin_path, f"must not duplicate plugin.json fields: {', '.join(sorted(forbidden))}")
            plugin_id = expect_type(errors, plugin, "id", str, plugin_path)
            if plugin_id:
                if not NAME_RE.match(plugin_id):
                    fail(errors, plugin_path, "id must be lowercase kebab-case")
                if plugin_id in seen_plugins:
                    fail(errors, plugin_path, f"duplicate plugin id '{plugin_id}'")
                seen_plugins.add(plugin_id)
            expect_type(errors, plugin, "displayName", str, plugin_path)
            expect_type(errors, plugin, "description", str, plugin_path)
            repository = expect_type(errors, plugin, "repository", str, plugin_path)
            if repository and not valid_url(repository):
                fail(errors, plugin_path, "repository must be a URL")
            latest = expect_type(errors, plugin, "latestVersion", str, plugin_path)
            if latest and not STRICT_SEMVER_RE.match(latest):
                fail(errors, plugin_path, "latestVersion must be semantic versioning")
            default_enabled = plugin.get("defaultEnabled")
            if default_enabled is not None and not isinstance(default_enabled, bool):
                fail(errors, plugin_path, "defaultEnabled must be boolean")
            versions = expect_type(errors, plugin, "versions", list, plugin_path)
            version_values = set()
            if isinstance(versions, list):
                if not versions:
                    fail(errors, plugin_path, "versions must not be empty")
                for version_index, version in enumerate(versions):
                    version_path = f"{plugin_path}.versions[{version_index}]"
                    if not isinstance(version, dict):
                        fail(errors, version_path, "must be an object")
                        continue
                    forbidden = FORBIDDEN_MANIFEST_FIELDS & set(version)
                    if forbidden:
                        fail(errors, version_path, f"must not duplicate plugin.json fields: {', '.join(sorted(forbidden))}")
                    version_value = expect_type(errors, version, "version", str, version_path)
                    if version_value:
                        if not STRICT_SEMVER_RE.match(version_value):
                            fail(errors, version_path, "version must be semantic versioning")
                        if version_value in version_values:
                            fail(errors, version_path, f"duplicate version '{version_value}'")
                        version_values.add(version_value)
                    package = expect_type(errors, version, "package", dict, version_path)
                    if isinstance(package, dict):
                        download_url = expect_type(errors, package, "downloadUrl", str, f"{version_path}.package")
                        checksum_url = expect_type(errors, package, "checksumUrl", str, f"{version_path}.package")
                        if download_url and not valid_url(download_url):
                            fail(errors, version_path, "downloadUrl must be a URL")
                        if checksum_url and not valid_url(checksum_url):
                            fail(errors, version_path, "checksumUrl must be a URL")
                        if "sha256" in package:
                            fail(errors, version_path, "package must use checksumUrl, not inline sha256")
                    source = expect_type(errors, version, "source", dict, version_path)
                    if isinstance(source, dict):
                        source_type = expect_type(errors, source, "type", str, f"{version_path}.source")
                        if source_type not in {"archive", "path"}:
                            fail(errors, version_path, "source.type must be archive or path")
            if latest and version_values and latest not in version_values:
                fail(errors, plugin_path, "latestVersion must match one of versions[].version")

    if checksum_path is not None:
        validate_checksum_file(path, checksum_path, errors)
    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate a Jottr plugins.json catalog.")
    parser.add_argument("path", nargs="?", default="plugins.json")
    parser.add_argument("--checksum", default="plugins.json.sha256")
    args = parser.parse_args()
    path = Path(args.path)
    checksum = Path(args.checksum) if args.checksum else None
    errors = validate_index(path, checksum)
    if errors:
        print("plugins.json validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

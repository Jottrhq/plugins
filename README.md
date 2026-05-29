# Jottr Plugin Index

This repository is the central plugin index consumed by Jottr.

`plugin-sources.json` is the small human-maintained file. Add plugin repositories there, then CI generates the app-facing `plugins.json` and `plugins.json.sha256` from each plugin repository's latest GitHub release.

`plugins.json` lists each plugin, available version, repository URL, release download URL, and checksum URL for the zip package. `plugins.json.sha256` checksums the current index content so the app can reject a tampered index after fetching it.

Plugin packages are released from their own repositories. Each plugin repo owns its version history, release tag, zip package, and `.zip.sha256` asset, so plugin releases do not depend on the main app tag.

## Add A Plugin

For a Jottrhq plugin repo, add only the repository name:

```json
{
  "schemaVersion": 1,
  "defaultOwner": "Jottrhq",
  "plugins": [
    "browser-plugin",
    "rss-feed-plugin",
    "mermaid-charts-plugin"
  ]
}
```

For another owner, use `owner/repo` or a GitHub URL:

```json
"some-org/custom-plugin"
```

You can also run the sync workflow manually and pass `plugin=rss-feed-plugin`. The workflow will add it to `plugin-sources.json`, regenerate `plugins.json`, verify release package checksums, update `plugins.json.sha256`, and open a PR.

## Sync Locally

```bash
python scripts/sync-plugin-index.py
```

The sync script reads each plugin repo's latest release, loads that release tag's `plugin.json`, finds `<plugin-name>-<version>.zip` and `<plugin-name>-<version>.zip.sha256`, verifies the zip checksum, and writes the generated registry.

Use `GITHUB_TOKEN` for private repos or higher API limits:

```bash
GITHUB_TOKEN=... python scripts/sync-plugin-index.py
```

## Validate

```bash
python scripts/validate-plugins-json.py plugins.json --checksum plugins.json.sha256
```

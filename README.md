# Jottr Plugin Index

This repository is the central plugin index consumed by Jottr.

`plugins.json` lists each plugin, available versions, repository URL, release download URL, and SHA-256 checksum for the zip package. `plugins.json.sha256` signs the current index content with a plain SHA-256 checksum so the app can reject a tampered index after fetching it.

Plugin packages are released from their own repositories. Each plugin repo owns its version history and release tags, so plugin releases do not depend on the main app tag.

## Validate

```bash
python scripts/validate-plugins-json.py plugins.json --checksum plugins.json.sha256
```

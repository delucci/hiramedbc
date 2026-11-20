# HirameDBC

**English** | [日本語](README.ja.md)

A CLI that runs the official MySQL Docker image as disposable containers, so you can switch between multiple versions of the MySQL client tools (`mysql` / `mysqldump`). Works on macOS and Windows.

- No local MySQL client installation required.
- Mix and match connections and MySQL versions freely (`hirame c 5.7 legacy` / `hirame c 8.4 production`).
- Choose where each password lives: enter it every time you connect, save it in the OS keychain (macOS Keychain / Windows Credential Manager), or save it in plaintext in the config file.
- Credentials are never passed as command-line arguments or environment variables. They reach the container as a MySQL option file.

> Install, upgrade, and uninstall with the **package name `hiramedbc`**. For everyday use, run the **command `hirame`**.

## Requirements

| Requirement | How to get it |
|---|---|
| [uv](https://docs.astral.sh/uv/) | macOS: `brew install uv` / Windows: `winget install astral-sh.uv` |
| A Docker-compatible runtime (the `docker` command must work) | macOS: Docker Desktop / OrbStack / Colima / Rancher Desktop<br>Windows: Docker Desktop / Rancher Desktop (requires WSL2) |
| Network access to your MySQL server | - |

You do not need to install Python or git separately (uv provides Python). Docker Desktop may require a paid subscription depending on your organization's size. Check the current terms before adopting it.

## Installation

```bash
uv tool install hiramedbc            # Install
uv tool install hiramedbc==0.1.0     # Install a specific version
uv tool upgrade hiramedbc            # Upgrade
uv tool uninstall hiramedbc          # Uninstall
uvx --from hiramedbc hirame          # Run once without installing
```

If `hirame` is not found right after installation, run `uv tool update-shell` and then open a new terminal.

## Getting started

```bash
hirame init           # Environment check → create config file → add connections → pull images
hirame c production   # Connect
```

`init` runs the following steps in order. If Docker is unavailable, it prints a warning and still continues through the configuration steps.

1. Environment check (same as `hirame doctor`)
2. Create the config file (you are asked for the default MySQL version)
3. Add connections (you can add several in a row)
4. Choose whether to pull the images for the added connections now

## Commands

| Command | Alias | Description |
|---|---|---|
| `hirame init` | - | Initial setup |
| `hirame connect [VERSION] ALIAS [DATABASE]` | `c` | Connect to a database |
| `hirame add [ALIAS]` | `a` | Add a connection (interactive) |
| `hirame list` | `ls` | List saved connections |
| `hirame edit ALIAS` | `e` | Edit a connection (interactive; press Enter to keep the current value) |
| `hirame remove ALIAS [-y]` | `rm` | Remove a connection and its password from the keychain (`-y` skips confirmation) |
| `hirame dump [VERSION] ALIAS DATABASE OUTPUT [-f] [-- EXTRA_OPTIONS_AND_TABLES]` | `d` | Create a dump with `mysqldump` (`-f` skips the overwrite confirmation) |
| `hirame config path` | - | Show the path of the config file in use |
| `hirame config edit` | - | Open the config file in your editor |
| `hirame doctor [--pull [VERSION...]] [--clean]` | `dr` | Check the environment, pre-pull images, and remove stopped leftover containers |
| `hirame version` | `v` | Show the version |

`connect` is optional: `hirame production` / `hirame 8.4 production` are the same as `hirame connect ...`. Every command accepts `--config <PATH>`.

### Examples

```bash
hirame c production               # Connect with the configured version
hirame c 8.4 production my_db     # Specify the version and database
hirame c production < script.sql  # Pipe in an SQL file (save the username in the connection first)

hirame d production my_db backup.sql                         # Dump
hirame d production my_db - > backup.sql                     # Dump to stdout
hirame d production my_db backup.sql -- --no-data users      # Pass options and table names to mysqldump

hirame doctor --pull              # Pull images for every version used in the config
hirame doctor --pull 5.7 8.4      # Pull specific versions
```

### How the version is chosen

1. The `VERSION` command argument
2. The connection's `version`
3. `defaults.version` in the config file

When connecting, `hirame` shows which version it uses and where it came from (argument / connection / default). If the first argument is version-like (`8`, `8.4`, `8.0.36`, and so on), it is treated as a version; otherwise it is treated as a connection name. As a result, version-like names and command names (`list`, `ls`, and so on) cannot be used as connection names.

## Credentials

The username is either "saved" or "ask every time". The password can be "ask every time", saved in the OS keychain, or saved in plaintext in the config file.

| Username | Password | Typical use | Prompted on connect |
|---|---|---|---|
| Saved | Saved (config file) | Local development where you edit the password often | Nothing |
| Saved | Saved (keychain) | Shared development account | Nothing |
| Saved | Ask every time | You prefer not to store the password, or it rotates regularly | Password only |
| Ask every time | Ask every time | Personal accounts, or issued temporary accounts | Both |

- For production connections, you can choose not to save the password and enter it every time instead. When adding a connection, saving the password defaults to no.
- To skip saved values for a single run, pass `--ask-user` (which also prompts for the password) or `--ask-password`.
- A password saved in the config file is stored in plaintext. Only use it on a machine where just you can read the file (on macOS the file is created with 0600 permissions; `hirame doctor` warns if others can read it).
- To change a password saved in the config file, edit it with `hirame config edit` (or re-run `hirame edit`).
- `list` never shows password values. It only shows where each password is stored (keychain / config file).
- On macOS, the first keychain access shows a permission dialog. Choose "Always Allow". The dialog may appear again after uv updates Python.

## Config file

| Priority | Location |
|---|---|
| 1 | `--config <PATH>` |
| 2 | The `HIRAMEDBC_CONFIG` environment variable |
| 3 | `~/.hiramedbc/config.json` (Windows: `%USERPROFILE%\.hiramedbc\config.json`) |

```json
{
  "connections": {
    "development": { "host": "dev-db.example.com", "port": 3306, "user": "app_user", "password": "keyring", "version": "8.0" },
    "production": {
      "host": "prod-db.example.com", "port": 3306, "user": "", "password": "", "version": "8.4",
      "ssl": { "mode": "VERIFY_IDENTITY", "ca": "/path/to/ca.pem" }
    }
  },
  "defaults": { "version": "8.4" }
}
```

| Key | Description |
|---|---|
| `host` | Required. The MySQL server host |
| `port` | Defaults to `3306` |
| `user` | A value means saved. Empty means ask every time |
| `password` | Empty means ask every time. `"keyring"` reads the password from the keychain. Any other value is used as the password itself (plaintext). The literal password `keyring` therefore cannot be saved in the config file |
| `version` | The MySQL version (image tag) for this connection |
| `ssl` | Optional. `mode` (`DISABLED` / `PREFERRED` / `REQUIRED` / `VERIFY_CA` / `VERIFY_IDENTITY`) and `ca` / `cert` / `key` (paths on the host). Certificates are mounted into the container read-only |
| `defaults.version` | The default version shared by all connections |

You cannot combine an ask-every-time username with a saved password, because passwords are stored per connection. On macOS, the config file is created with owner-only read/write permissions (0600).

## Notes

- **Connecting to MySQL on the host machine**: Inside a container, `localhost` / `127.0.0.1` refers to the container itself. Use `host.docker.internal` instead.
- **Apple Silicon**: Versions without an arm64 build of the official image (such as 5.7) run under amd64 emulation. They do not work if Rosetta / QEMU is disabled in your runtime (check with `hirame doctor`).
- **Container cleanup**: The container is removed when the session ends, on error, and on Ctrl+C. Containers left behind (for example, when a terminal is force-closed) are detected by `hirame doctor`. Stopped ones can be removed with `hirame doctor --clean`. Leftover containers that are still running might be in use from another terminal, so they are not removed automatically; their names are shown instead. If they are not in use, remove them with `docker rm -f <name>`.
- **Dump files**: Dumps are saved with owner-only read/write permissions. If a dump fails or is interrupted, any existing file is left unchanged. Arguments after `--` are passed as `mysqldump ... <database> <extra arguments>` (table names are allowed). Extra arguments without `--` cause an error.
- **Unsupported password characters**: Passwords that contain both double quotes (`"`) and single quotes (`'`), or that contain a NUL character, are not supported (they cannot be represented safely in a MySQL option file).

## Development

```bash
uv sync                                         # Install dependencies
HIRAMEDBC_CONFIG=./config/config.json uv run hirame ls   # Run with the in-repo config (config/config.json is gitignored)
uv run pytest                                   # Run tests
uv run ruff check . && uv run ruff format --check .
```

- Releases: Pushing a `v*` tag publishes to PyPI through GitHub Actions Trusted Publishing (no API token is stored in the repository).

## License

[MIT](LICENSE)

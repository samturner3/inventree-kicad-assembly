"""Where the plugin finds the InvenTree host and API token.

Checked in order, first hit wins:

  1. INVENTREE_HOST / INVENTREE_TOKEN in the environment
  2. HOST / TOKEN in the environment (matches the scripts this grew out of)
  3. a .env file beside the plugin package
  4. ~/.config/inventree-kicad-assembly.env

The file forms are there because KiCad is normally launched from the desktop,
which inherits none of a shell's exported variables -- so environment-only
configuration would work from a terminal and mysteriously fail from the dock.

A token is a credential: keep the file readable only by you, and out of any
repository.
"""

import os

CONFIG_NAME = "inventree-kicad-assembly.env"


def _candidate_files():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    yield os.path.join(here, ".env")
    yield os.path.join(os.path.expanduser("~"), ".config", CONFIG_NAME)
    yield os.path.join(os.path.expanduser("~"), f".{CONFIG_NAME}")


def _read_env_file(path):
    values = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    except OSError:
        return {}
    return values


def load_settings():
    """(host, token), either possibly None."""
    host = os.environ.get("INVENTREE_HOST") or os.environ.get("HOST")
    token = os.environ.get("INVENTREE_TOKEN") or os.environ.get("TOKEN")
    if host and token:
        return host, token

    for path in _candidate_files():
        if not os.path.isfile(path):
            continue
        values = _read_env_file(path)
        host = host or values.get("INVENTREE_HOST") or values.get("HOST")
        token = token or values.get("INVENTREE_TOKEN") or values.get("TOKEN")
        if host and token:
            return host, token

    return host, token


def settings_help():
    return (
        "InvenTree host and API token not configured.\n\n"
        f"Create ~/.config/{CONFIG_NAME} containing:\n\n"
        "    INVENTREE_HOST=https://inventree.example.com\n"
        "    INVENTREE_TOKEN=your-api-token\n\n"
        "Get a token from InvenTree under Settings → Account → API Tokens.\n"
        "Keep the file private; a token carries your account's permissions."
    )

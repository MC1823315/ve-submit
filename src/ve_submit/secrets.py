"""Participant API keys from the environment or a private env file.

The model checkout is never searched. A `.env` there could be packaged into a submission.
"""
import os
from pathlib import Path
import sys

import getpass

from . import secure

YUKON_API_TOKEN = "YUKON_API_TOKEN"
VE_API_KEY = "VE_API_KEY"


class SecretFileError(Exception):
    """The configured env file cannot be used. The message contains no key material."""


def parse_dotenv(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def dotenv_path(environ, home):
    explicit = environ.get("VE_SUBMIT_ENV", "").strip()
    if explicit:
        return Path(explicit), True
    return Path(home) / ".config" / "ve-submit" / ".env", False


def load_file(path):
    try:
        raw = secure.read(path, 16384, private=True)
        text = raw.decode("utf-8")
    except (OSError, UnicodeError, ValueError):
        raise SecretFileError("API key file must be a private UTF-8 file owned by you.") from None
    return parse_dotenv(text)


def configured_values(environ=None, home=None):
    environ = os.environ if environ is None else environ
    home = Path.home() if home is None else home
    path, explicit = dotenv_path(environ, home)
    if not path.is_file():
        if explicit:
            raise SecretFileError("VE_SUBMIT_ENV does not point at a private env file.")
        return {}
    return load_file(path)


def has_secret(name, environ, file_values):
    return lookup(name, environ, file_values) is not None


def lookup(name, environ, file_values):
    for source in (environ, file_values):
        raw = source.get(name)
        if type(raw) is str and raw.strip():
            return raw.strip()
    return None


def prompt_secret(label, name, environ, file_values):
    found = lookup(name, environ, file_values)
    if found is None:
        return getpass.getpass(f"{label} (hidden): ")
    origin = "the environment" if type(environ.get(name)) is str and environ.get(name).strip() else "the private env file"
    print(f"Using {label} from {origin}.", file=sys.stderr)
    return found

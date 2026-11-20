"""Authorize this Trakt app and update its active token file."""

import os
import sys
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values

API_URL = "https://api.trakt.tv"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def write_env(path: Path, values: dict[str, str], *, remove: bool = False) -> None:
    """Update selected keys atomically while preserving unrelated env content."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found: set[str] = set()
    updated: list[str] = []
    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key in values:
            found.add(key)
            if not remove:
                updated.append(f"{key}={values[key]}")
        else:
            updated.append(line)
    if not remove:
        updated.extend(f"{key}={value}" for key, value in values.items() if key not in found)
    mode = path.stat().st_mode
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        temporary.write("\n".join(updated) + "\n")
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, mode)
    temporary_path.replace(path)


def save_tokens(
    env_path: Path, config: dict[str, Any], access_token: str, refresh_token: str
) -> Path:
    """Save to tokens.env when configured and present; otherwise use .env."""
    values = {"TRAKT_ACCESS_TOKEN": access_token, "TRAKT_REFRESH_TOKEN": refresh_token}
    db_path = Path(str(config.get("DB_PATH", "/data/prices.db")))
    token_path = db_path.parent / "tokens.env"
    if token_path.is_file():
        write_env(token_path, values)
        write_env(env_path, values, remove=True)
        return token_path
    write_env(env_path, values)
    return env_path


def authorize(env_path: Path = ENV_PATH) -> None:
    config = dotenv_values(env_path)
    client_id = config.get("TRAKT_CLIENT_ID")
    client_secret = config.get("TRAKT_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError(".env must contain TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET.")

    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
    }
    response = requests.post(
        f"{API_URL}/oauth/device/code",
        headers=headers,
        json={"client_id": client_id},
        timeout=30,
    )
    response.raise_for_status()
    device: dict[str, Any] = response.json()
    verification_url = device["verification_url"]
    print(f"Opening {verification_url}")
    print(f"Enter this Trakt code: {device['user_code']}")
    print(f"Waiting up to {device['expires_in']} seconds for approval.")
    if not webbrowser.open(verification_url):
        print("Open the verification URL above in a browser.")

    deadline = time.monotonic() + int(device["expires_in"])
    interval = int(device["interval"])
    while time.monotonic() < deadline:
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        token_response = requests.post(
            f"{API_URL}/oauth/device/token",
            headers=headers,
            json={
                "code": device["device_code"],
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )
        if token_response.status_code == 200:
            tokens = token_response.json()
            saved_path = save_tokens(
                env_path, config, tokens["access_token"], tokens["refresh_token"]
            )
            print(f"Trakt authorization saved to {saved_path}.")
            return
        if token_response.status_code == 400:
            continue
        if token_response.status_code == 429:
            interval += 5
            continue
        token_response.raise_for_status()
        raise RuntimeError(f"Trakt device authorization failed ({token_response.status_code}).")

    raise TimeoutError("Trakt authorization expired. Run this script again to get a new code.")


def main() -> None:
    try:
        authorize()
    except (OSError, requests.RequestException, KeyError, ValueError, TimeoutError) as exc:
        print(f"Authorization failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

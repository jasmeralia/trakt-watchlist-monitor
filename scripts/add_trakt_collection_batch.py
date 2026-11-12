"""Add a reviewed CSV of digital movies to a Trakt collection.

The CSV must contain ``amazon_title``, ``amazon_url``, ``trakt_title``,
``trakt_year``, ``trakt_id``, and ``trakt_slug`` columns. The default action is
preview-only; pass ``--commit`` to write. Credentials come from the project's
normal settings and are never printed.
"""

# Project modules are imported after adding the checkout to sys.path.
# ruff: noqa: E402
# pylint: disable=wrong-import-position

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))
sys.path.insert(0, str(script_dir))

from trakt_watchlist_monitor import trakt
from trakt_watchlist_monitor.config import settings

from add_trakt_collection import (  # pylint: disable=wrong-import-order
    MEDIA_TYPES,
    RESOLUTIONS,
)


@dataclass(frozen=True)
class MovieTarget:
    amazon_title: str
    amazon_url: str
    trakt_title: str
    trakt_year: int
    trakt_id: int
    trakt_slug: str

    @property
    def label(self) -> str:
        return f"{self.trakt_title} ({self.trakt_year})"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add a reviewed batch of digital movies to your Trakt collection."
    )
    parser.add_argument("--input", required=True, type=Path, help="Reviewed CSV of movies.")
    parser.add_argument(
        "--resolution",
        choices=RESOLUTIONS,
        default="hd_720p",
    )
    parser.add_argument("--media-type", default="digital", choices=MEDIA_TYPES)
    parser.add_argument("--audio-channels", default="2.0")
    parser.add_argument("--commit", action="store_true", help="Write the items to Trakt.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt. Only valid together with --commit.",
    )
    return parser.parse_args(argv)


def load_targets(path: Path) -> list[MovieTarget]:
    required = {
        "amazon_title",
        "amazon_url",
        "trakt_title",
        "trakt_year",
        "trakt_id",
        "trakt_slug",
    }
    targets: list[MovieTarget] = []
    seen_ids: set[int] = set()
    with path.open(encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for line_number, row in enumerate(reader, start=2):
            try:
                target = MovieTarget(
                    amazon_title=(row["amazon_title"] or "").strip(),
                    amazon_url=(row["amazon_url"] or "").strip(),
                    trakt_title=(row["trakt_title"] or "").strip(),
                    trakt_year=int(row["trakt_year"]),
                    trakt_id=int(row["trakt_id"]),
                    trakt_slug=(row["trakt_slug"] or "").strip(),
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid movie row at CSV line {line_number}.") from exc
            if not all(
                (
                    target.amazon_title,
                    target.amazon_url,
                    target.trakt_title,
                    target.trakt_slug,
                )
            ):
                raise ValueError(f"Missing title, URL, or Trakt slug at CSV line {line_number}.")
            if target.trakt_id in seen_ids:
                raise ValueError(f"Duplicate Trakt movie ID at CSV line {line_number}.")
            seen_ids.add(target.trakt_id)
            targets.append(target)
    if not targets:
        raise ValueError("The input CSV contains no movies.")
    return targets


def fetch_movie(target: MovieTarget) -> dict[str, Any]:
    response = trakt._request_with_refresh(  # pylint: disable=protected-access
        "GET", f"/movies/{target.trakt_id}?extended=full"
    )
    movie = response.json()
    ids = movie.get("ids", {})
    if ids.get("trakt") != target.trakt_id or ids.get("slug") != target.trakt_slug:
        raise ValueError(f"Trakt ID and slug disagree for {target.label}.")
    if movie.get("title") != target.trakt_title or movie.get("year") != target.trakt_year:
        raise ValueError(f"Trakt title/year changed for {target.label}; refusing to add it.")
    return dict(movie)


def fetch_collection_ids() -> set[int]:
    collection = trakt._get(  # pylint: disable=protected-access
        f"/users/{settings.trakt_username}/collection/movies"
    )
    ids = {trakt._item_trakt_id(item) for item in collection}  # pylint: disable=protected-access
    ids.discard(None)
    return {item_id for item_id in ids if isinstance(item_id, int)}


def build_payload(
    targets: list[MovieTarget],
    *,
    media_type: str,
    resolution: str,
    audio_channels: str,
) -> dict[str, list[dict[str, Any]]]:
    collected_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {
        "movies": [
            {
                "ids": {"trakt": target.trakt_id},
                "collected_at": collected_at,
                "media_type": media_type,
                "resolution": resolution,
                "audio_channels": audio_channels,
            }
            for target in targets
        ]
    }


def confirm_send() -> bool:
    return input("Add these movies to your Trakt collection? (y/n) ").strip().lower() == "y"


def run(args: argparse.Namespace) -> bool:
    if args.force and not args.commit:
        raise ValueError("--force requires --commit.")
    targets = load_targets(args.input)
    if len(targets) > 100:
        raise ValueError("Trakt accepts at most 100 movies per collection request.")

    for target in targets:
        fetch_movie(target)

    collection_ids = fetch_collection_ids()
    already_present = [target.label for target in targets if target.trakt_id in collection_ids]
    if already_present:
        raise ValueError(
            "These movies are already in the collection; refusing to overwrite metadata: "
            + ", ".join(already_present)
        )

    payload = build_payload(
        targets,
        media_type=args.media_type,
        resolution=args.resolution,
        audio_channels=args.audio_channels,
    )
    print(f"Ready to add {len(targets)} movies ({args.media_type}, {args.resolution}).")
    for target in targets:
        print(f"  {target.label} — {target.amazon_url}")
    if not args.commit:
        print("Preview only; nothing was sent. Pass --commit to write to Trakt.")
        return True
    if not args.force and not confirm_send():
        print("Cancelled; nothing was sent.")
        return True

    response = trakt._request_with_refresh(  # pylint: disable=protected-access
        "POST", "/sync/collection", json=payload
    ).json()
    added = response.get("added", {}).get("movies", 0)
    print(f"Trakt reported {added} movies added.")
    verified_ids = fetch_collection_ids()
    missing = [target.label for target in targets if target.trakt_id not in verified_ids]
    if missing:
        print("Verification failed for: " + ", ".join(missing), file=sys.stderr)
        return False
    print(f"Verified all {len(targets)} movies in your Trakt collection.")
    return True


def main() -> None:
    args = parse_args()
    try:
        success = run(args)
    except requests.RequestException as exc:
        print(f"Trakt API request failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

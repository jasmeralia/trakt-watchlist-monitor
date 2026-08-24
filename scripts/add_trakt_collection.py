"""Manually add/re-tag a single movie or episode in the Trakt collection.

Useful when Sonarr (or any other tool) fails to auto-add an item with the
correct metadata tags -- something the Trakt website no longer lets you fix
manually. Credentials are read via trakt_watchlist_monitor/trakt.py, which reads
the same .env file as the rest of this project (see trakt_watchlist_monitor/config.py).

Usage:
    # preview only, nothing sent
    python scripts/add_trakt_collection.py --url "https://app.trakt.tv/movies/nobody-2021" \\
        --resolution uhd_4k --audio dolby_atmos --audio-channels 7.1 --hdr dolby_vision

    # check what is currently stored on Trakt, no changes
    python scripts/add_trakt_collection.py --url "..." --check-only

    # actually write it
    python scripts/add_trakt_collection.py --url "..." --resolution uhd_4k --commit
"""

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trakt_watchlist_monitor import trakt  # noqa: E402  pylint: disable=wrong-import-position

MEDIA_TYPES = ("digital", "bluray", "hddvd", "dvd", "vcd", "vhs", "betamax", "laserdisc")
RESOLUTIONS = (
    "uhd_4k",
    "hd_1080p",
    "hd_1080i",
    "hd_720p",
    "sd_480p",
    "sd_480i",
    "sd_576p",
    "sd_576i",
)
AUDIO_CODECS = (
    "lpcm",
    "mp3",
    "aac",
    "ogg",
    "wma",
    "dts",
    "dts_ma",
    "dts_hr",
    "dts_x",
    "auro_3d",
    "dolby_digital",
    "dolby_digital_plus",
    "dolby_atmos",
    "dolby_truehd",
    "dolby_prologic",
)
AUDIO_CHANNELS = (
    "1.0",
    "2.0",
    "2.1",
    "3.0",
    "3.1",
    "4.0",
    "4.1",
    "5.0",
    "5.1",
    "5.1.2",
    "5.1.4",
    "6.1",
    "7.1",
    "7.1.2",
    "7.1.4",
    "9.1",
    "10.1",
)
HDR_FORMATS = ("dolby_vision", "hdr10", "hdr10_plus", "hlg")

MOVIE_URL_RE = re.compile(r"trakt\.tv/movies/([^/?]+)")
SHOW_URL_RE = re.compile(r"trakt\.tv/shows/([^/?]+)")
OLD_STYLE_SEASON_EPISODE_RE = re.compile(r"/seasons/(\d+)/episodes/(\d+)")
SEASON_QUERY_RE = re.compile(r"[?&]season=(\d+)")
EPISODE_QUERY_RE = re.compile(r"[?&]episode=(\d+)")


class UsageError(Exception):
    """Raised for invalid argument combinations or unresolved item identity."""


@dataclass(frozen=True)
class ItemIdentity:
    is_movie: bool
    show_slug: str | None = None
    season: int | None = None
    episode: int | None = None
    movie_slug: str | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manually add or re-tag a movie/episode in your Trakt collection.",
    )
    parser.add_argument("--url", help="Full app.trakt.tv URL for a movie or show/episode.")
    parser.add_argument("--show-slug", help="Show slug (use with --season and --episode).")
    parser.add_argument("--season", type=int, help="Season number.")
    parser.add_argument("--episode", type=int, help="Episode number.")
    parser.add_argument("--movie-slug", help="Movie slug.")
    parser.add_argument("--media-type", choices=MEDIA_TYPES, default="digital")
    parser.add_argument("--resolution", choices=RESOLUTIONS, default="uhd_4k")
    parser.add_argument("--audio", choices=AUDIO_CODECS, default=None)
    parser.add_argument("--audio-channels", choices=AUDIO_CHANNELS, default="2.0")
    parser.add_argument("--hdr", choices=HDR_FORMATS, default=None)
    parser.add_argument("--is-3d", action="store_true", help="Flag the collected item as 3D.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the y/n confirmation prompt. Only valid together with --commit.",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only look up the item and print its current collection metadata.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Actually write to Trakt. Without this, the payload is only previewed.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.check_only and args.commit:
        raise UsageError("--check-only and --commit cannot be used together.")
    if args.check_only and args.force:
        raise UsageError("--check-only and --force cannot be used together.")
    if args.force and not args.commit:
        raise UsageError("--force requires --commit.")


def resolve_identity(args: argparse.Namespace) -> ItemIdentity:
    show_slug, season, episode, movie_slug = (
        args.show_slug,
        args.season,
        args.episode,
        args.movie_slug,
    )

    if not args.url and not show_slug and not movie_slug:
        raise UsageError(
            "You must supply --url, or --movie-slug, or --show-slug/--season/--episode."
        )

    is_movie = False
    if args.url:
        if movie_match := MOVIE_URL_RE.search(args.url):
            is_movie = True
            movie_slug = movie_slug or movie_match.group(1)
        elif show_match := SHOW_URL_RE.search(args.url):
            is_movie = False
            show_slug = show_slug or show_match.group(1)
            if old_style := OLD_STYLE_SEASON_EPISODE_RE.search(args.url):
                season = season or int(old_style.group(1))
                episode = episode or int(old_style.group(2))
            if not season and (season_match := SEASON_QUERY_RE.search(args.url)):
                season = int(season_match.group(1))
            if not episode and (episode_match := EPISODE_QUERY_RE.search(args.url)):
                episode = int(episode_match.group(1))
        else:
            print("Could not parse --url; falling back to --show-slug/--movie-slug.")
    elif movie_slug:
        is_movie = True

    if is_movie:
        if not movie_slug:
            raise UsageError("Missing movie slug. Nothing to do.")
    elif not show_slug or not season or not episode:
        raise UsageError("Missing show slug, season, or episode number. Nothing to do.")

    return ItemIdentity(is_movie, show_slug, season, episode, movie_slug)


def _trakt_request(
    method: str, path: str, *, json_body: dict[str, Any] | None = None
) -> requests.Response:
    return trakt._request_with_refresh(  # pylint: disable=protected-access
        method, path, json=json_body
    )


def fetch_item(identity: ItemIdentity) -> dict[str, Any]:
    if identity.is_movie:
        assert identity.movie_slug is not None
        path = f"/movies/{identity.movie_slug}?extended=full"
    else:
        assert identity.show_slug is not None
        assert identity.season is not None
        assert identity.episode is not None
        path = (
            f"/shows/{identity.show_slug}/seasons/{identity.season}"
            f"/episodes/{identity.episode}?extended=full"
        )

    try:
        response = _trakt_request("GET", path)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise UsageError("Not found on Trakt. Double check the slug/season/episode.") from exc
        raise
    return dict(response.json())


def build_label(identity: ItemIdentity, item_info: dict[str, Any]) -> str:
    if identity.is_movie:
        return f"Movie '{identity.movie_slug}' ({item_info.get('title')} {item_info.get('year')})"
    return (
        f"Episode S{identity.season:02d}E{identity.episode:02d} of "
        f"'{identity.show_slug}' - {item_info.get('title')}"
    )


def fetch_collection_movie_entry(movie_slug: str) -> dict[str, Any] | None:
    response = _trakt_request("GET", "/sync/collection/movies?extended=full")
    for entry in response.json():
        if entry.get("movie", {}).get("ids", {}).get("slug") == movie_slug:
            return dict(entry)
    return None


def fetch_collection_episode_entry(
    show_slug: str, season: int, episode: int
) -> dict[str, Any] | None:
    response = _trakt_request("GET", "/sync/collection/shows?extended=full")
    for show_entry in response.json():
        if show_entry.get("show", {}).get("ids", {}).get("slug") != show_slug:
            continue
        for season_entry in show_entry.get("seasons", []):
            if season_entry.get("number") != season:
                continue
            for episode_entry in season_entry.get("episodes", []):
                if episode_entry.get("number") == episode:
                    return dict(episode_entry)
    return None


def fetch_collection_entry(identity: ItemIdentity) -> dict[str, Any] | None:
    if identity.is_movie:
        assert identity.movie_slug is not None
        return fetch_collection_movie_entry(identity.movie_slug)
    assert identity.show_slug is not None
    assert identity.season is not None
    assert identity.episode is not None
    return fetch_collection_episode_entry(identity.show_slug, identity.season, identity.episode)


def print_collection_metadata(entry: dict[str, Any] | None, label: str) -> bool:
    if not entry:
        print(f"{label} is not in your collection.")
        return False

    meta = entry.get("metadata", {})
    print(f"{label} IS in your collection.")
    print(f"    Collected at    : {entry.get('collected_at')}")
    print(f"    Media type      : {meta.get('media_type')}")
    print(f"    Resolution      : {meta.get('resolution')}")
    print(f"    HDR             : {meta.get('hdr') or 'No'}")
    print(f"    Audio           : {meta.get('audio') or 'No'}")
    print(f"    Audio channels  : {meta.get('audio_channels')}")
    print(f"    3D              : {'Yes' if meta.get('3d') else 'No'}")
    return True


def build_payload(args: argparse.Namespace, identity: ItemIdentity) -> dict[str, Any]:
    item: dict[str, Any] = {
        "collected_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "media_type": args.media_type,
        "resolution": args.resolution,
        "audio_channels": args.audio_channels,
    }
    if args.audio:
        item["audio"] = args.audio
    if args.hdr:
        item["hdr"] = args.hdr
    if args.is_3d:
        item["3d"] = True

    if identity.is_movie:
        item = {"ids": {"slug": identity.movie_slug}, **item}
        return {"movies": [item]}

    item = {"number": identity.episode, **item}
    return {
        "shows": [
            {
                "ids": {"slug": identity.show_slug},
                "seasons": [{"number": identity.season, "episodes": [item]}],
            }
        ]
    }


def print_preview(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    key = "movies" if "movies" in payload else "shows"
    item = payload[key][0] if key == "movies" else payload["shows"][0]["seasons"][0]["episodes"][0]
    print("This will set:")
    print(f"    Collected at    : {item['collected_at']}")
    print(f"    Media type      : {args.media_type}")
    print(f"    Resolution      : {args.resolution}")
    print(f"    HDR             : {args.hdr or 'No'}")
    print(f"    Audio           : {args.audio or 'No'}")
    print(f"    Audio channels  : {args.audio_channels}")
    print(f"    3D              : {'Yes' if args.is_3d else 'No'}")


def confirm_send() -> bool:
    answer = input("Send this to your Trakt collection? (y/n) ")
    return answer.strip().lower() == "y"


def commit_and_verify(identity: ItemIdentity, payload: dict[str, Any], label: str) -> bool:
    response = _trakt_request("POST", "/sync/collection", json_body=payload)
    result = response.json()
    count_key = "movies" if identity.is_movie else "episodes"

    if result.get("added", {}).get(count_key, 0) >= 1:
        print(f"Added to collection. (added.{count_key} = {result['added'][count_key]})")
    elif result.get("updated", {}).get(count_key, 0) >= 1:
        print(
            f"Metadata updated on the existing collection entry. "
            f"(updated.{count_key} = {result['updated'][count_key]})"
        )
    elif result.get("existing", {}).get(count_key, 0) >= 1:
        print("Already in your collection with identical metadata - nothing changed.")
    elif result.get("not_found", {}).get(count_key):
        print("Trakt reported it as not_found - check the ids/slug.")
        return False
    else:
        print(f"Unexpected response shape - inspect manually: {result}")

    return print_collection_metadata(fetch_collection_entry(identity), label)


def run(args: argparse.Namespace) -> bool:
    validate_args(args)
    identity = resolve_identity(args)

    item_info = fetch_item(identity)
    label = build_label(identity, item_info)
    print(f"Found: {label}")

    if args.check_only:
        return print_collection_metadata(fetch_collection_entry(identity), label)

    existing_entry = fetch_collection_entry(identity)
    if existing_entry:
        print(f"{label} is already in your collection - showing current metadata below.")
        print_collection_metadata(existing_entry, label)
    else:
        print(f"{label} is not yet in your collection - preparing to add it.")

    payload = build_payload(args, identity)
    print_preview(args, payload)

    if not args.commit:
        print("Preview only - nothing was sent. Pass --commit to actually write this to Trakt.")
        return True

    if not args.force and not confirm_send():
        print("Cancelled by user.")
        return True

    return commit_and_verify(identity, payload, label)


def main() -> None:
    args = parse_args()
    try:
        ok = run(args)
    except UsageError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    except requests.HTTPError as exc:
        print(f"Trakt API request failed: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

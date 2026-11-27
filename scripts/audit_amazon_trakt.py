"""Match an Amazon movie-library export to Trakt and prepare a review report."""

# Project modules are imported after adding the checkout to sys.path.
# ruff: noqa: E402
# pylint: disable=wrong-import-position

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from trakt_watchlist_monitor import db, trakt
from trakt_watchlist_monitor.config import settings

COLLECTION_WORDS = re.compile(r"\b(collection|triple feature|bundle|2-film|3-film|4-film)\b", re.I)
FORMAT_TAGS = re.compile(r"\b(?:4k|uhd|hd|bonus|x-ray|edition)\b", re.I)
YEAR_TAG = re.compile(r"\((19\d{2}|20\d{2})\)")
ASIN = re.compile(r"/(?:dp|detail)/([A-Z0-9]{10})(?:[/?#]|$)", re.I)
VALID_DECISIONS = {"pending", "add", "already_collected", "not_a_movie", "no_match", "stale"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument("--input", type=Path, help="Amazon export CSV to audit.")
    commands.add_argument("--export-reviewed", type=Path, help="Reviewed audit JSON to export.")
    parser.add_argument("--json", type=Path, help="Audit JSON output path.")
    parser.add_argument("--markdown", type=Path, help="Markdown review output path.")
    parser.add_argument("--output", type=Path, help="CSV path for --export-reviewed.")
    parser.add_argument(
        "--previous", type=Path, help="Previous audit JSON to retain review decisions."
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="Candidates per Amazon entry (default: 10)."
    )
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be a positive integer.")
    return args


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = FORMAT_TAGS.sub(" ", text)
    return re.sub(r"\W+", " ", text, flags=re.UNICODE).strip()


def title_year(title: str) -> int | None:
    match = YEAR_TAG.search(title)
    return int(match.group(1)) if match else None


def title_match_key(title: str) -> str:
    return normalize_title(YEAR_TAG.sub(" ", title))


def canonical_url(url: str) -> str:
    match = ASIN.search(urlsplit(url).path)
    if match:
        return match.group(1).upper()
    parts = urlsplit(url)
    return urlunsplit(
        (parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", "")
    )


def validate_decisions(data: dict[str, Any]) -> None:
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        decision = item.get("decision", "pending")
        if decision not in VALID_DECISIONS:
            raise ValueError(
                f"Invalid decision {decision!r} for {item.get('amazon_title', 'unknown item')}."
            )


def load_amazon_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as input_file:
        reader = csv.DictReader(input_file)
        names = {
            str(name).strip().casefold().replace("_", " "): name for name in reader.fieldnames or []
        }
        title_key = names.get("amazon title") or names.get("title")
        url_key = names.get("amazon url") or names.get("url")
        if not title_key or not url_key:
            raise ValueError("Amazon CSV needs Amazon title and Amazon URL columns.")
        for line_number, row in enumerate(reader, start=2):
            title = (row.get(title_key) or "").strip()
            url = (row.get(url_key) or "").strip()
            if not title and not url:
                continue
            if not title or not url:
                raise ValueError(f"Missing title or Amazon URL at CSV line {line_number}.")
            key = canonical_url(url)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"amazon_title": title, "amazon_url": url})
    if not rows:
        raise ValueError("Amazon CSV contains no library entries.")
    return rows


def previous_decisions(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_decisions(data)
    return {
        canonical_url(row["amazon_url"]): row
        for row in data.get("items", [])
        if isinstance(row, dict) and isinstance(row.get("amazon_url"), str)
    }


def search_candidates(
    title: str, limit: int, cached_movies: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    response = _search_response(title, limit)
    candidates: list[dict[str, Any]] = []
    for result in response.json():
        movie = result.get("movie", {})
        ids = movie.get("ids", {})
        trakt_id = ids.get("trakt")
        if not isinstance(trakt_id, int):
            continue
        candidate_title = str(movie.get("title", ""))
        slug = str(ids.get("slug", ""))
        candidates.append(
            {
                "trakt_id": trakt_id,
                "trakt_title": candidate_title,
                "trakt_year": movie.get("year"),
                "trakt_slug": slug,
                "trakt_url": f"https://trakt.tv/movies/{slug}" if slug else "",
                "overview": str(movie.get("overview", "")),
                "search_score": result.get("score"),
                "title_similarity": round(title_similarity(title, candidate_title), 3),
            }
        )
    candidate_ids = {candidate["trakt_id"] for candidate in candidates}
    normalized_title = title_match_key(title)
    for cached in cached_movies or []:
        if (
            not normalized_title
            or title_match_key(str(cached.get("trakt_title", ""))) != normalized_title
            or cached.get("trakt_id") in candidate_ids
        ):
            continue
        slug = str(cached.get("trakt_slug", ""))
        candidates.append(
            {
                **cached,
                "trakt_url": f"https://trakt.tv/movies/{slug}" if slug else "",
                "overview": "",
                "search_score": None,
                "title_similarity": 1.0,
                "cache_match": True,
            }
        )
        candidate_ids.add(cached["trakt_id"])
    return candidates


def title_similarity(left: str, right: str) -> float:
    normalized_left, normalized_right = normalize_title(left), normalize_title(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _search_response(title: str, limit: int) -> requests.Response:
    for attempt in range(4):
        try:
            return trakt._request_with_refresh(  # pylint: disable=protected-access
                "GET", f"/search/movie?query={quote(title)}&limit={limit}"
            )
        except requests.HTTPError as exc:
            response = exc.response
            code = response.status_code if response is not None else 0
            if code not in {429, 500, 502, 503, 504} or attempt == 3:
                raise
            retry_after = response.headers.get("Retry-After") if response is not None else None
            try:
                delay = min(30.0, max(0.0, float(retry_after))) if retry_after else 2**attempt
            except ValueError:
                delay = 2**attempt
            time.sleep(delay)
    raise RuntimeError("Trakt search retry loop ended unexpectedly.")


def collect_ids() -> set[int]:
    collection = trakt._get(  # pylint: disable=protected-access
        f"/users/{settings.trakt_username}/collection/movies"
    )
    return {
        trakt_id
        for item in collection
        if isinstance((trakt_id := trakt._item_trakt_id(item)), int)  # pylint: disable=protected-access
    }


# pylint: disable=too-many-return-statements
def classify_status(
    title: str, candidates: list[dict[str, Any]], previous: dict[str, Any] | None = None
) -> str:
    normalized = title_match_key(title)
    amazon_year = title_year(title)
    exact = [
        candidate
        for candidate in candidates
        if normalized and title_match_key(candidate["trakt_title"]) == normalized
    ]
    old = previous or {}
    selected_ids = old.get("selected_trakt_ids", [])
    if old.get("decision") in {"add", "already_collected"} and any(
        candidate.get("trakt_id") in selected_ids and candidate.get("in_collection")
        for candidate in candidates
    ):
        return "already_collected"
    if amazon_year is not None and any(
        candidate.get("in_collection") and candidate.get("trakt_year") == amazon_year
        for candidate in exact
    ):
        return "already_collected"
    if any(candidate["in_collection"] for candidate in exact):
        return "title_match_collected_review"
    if any(
        candidate["in_collection"] and candidate["title_similarity"] >= 0.8
        for candidate in candidates
    ):
        return "collected_candidate_review"
    if COLLECTION_WORDS.search(title):
        return "collection_or_bundle_review"
    if not candidates:
        return "no_candidates"
    if len(exact) == 1:
        return "exact_candidate_review"
    return "ambiguous_review"


# pylint: disable=too-many-locals,too-many-branches
def build_audit(
    rows: list[dict[str, str]],
    limit: int,
    previous: dict[str, dict[str, Any]],
    progress_path: Path | None = None,
) -> dict[str, Any]:
    collected = collect_ids()
    cached_movies: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
        try:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_items'"
            ).fetchone()
            if exists:
                cached_movies = db.get_current_watchlist_movies(conn)
            else:
                print(
                    "Warning: monitor database has no watchlist_items table; cache disabled.",
                    file=sys.stderr,
                )
        finally:
            conn.close()
    except sqlite3.Error as exc:
        print(f"Warning: monitor database unavailable; cache disabled: {exc}", file=sys.stderr)
    current_keys = {canonical_url(row["amazon_url"]) for row in rows}
    unmatched = [
        item.get("amazon_title", item.get("amazon_url", "unknown item"))
        for key, item in previous.items()
        if key not in current_keys and item.get("decision", "pending") != "pending"
    ]
    if unmatched:
        print(
            "Warning: previous decisions matched no current row: " + ", ".join(unmatched),
            file=sys.stderr,
        )
    items: list[dict[str, Any]] = []
    for row in rows:
        old = previous.get(canonical_url(row["amazon_url"]), {})
        try:
            candidates = search_candidates(row["amazon_title"], limit, cached_movies)
        except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
            # Keep the reviewed decision so a transient search failure never discards it; with no
            # candidates, export skips an "add" until a later audit refreshes them.
            items.append(
                {
                    **row,
                    "audit_status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "decision": old.get("decision", "pending"),
                    "selected_trakt_ids": old.get("selected_trakt_ids", []),
                    "review_note": old.get("review_note", ""),
                    "candidates": [],
                }
            )
            if progress_path:
                _write_json(progress_path, {"schema_version": 1, "items": items})
            continue
        for candidate in candidates:
            candidate["in_collection"] = candidate["trakt_id"] in collected
        status = classify_status(row["amazon_title"], candidates, old)
        decision = old.get("decision", "pending")
        selected_ids = old.get("selected_trakt_ids", [])
        by_id = {candidate["trakt_id"]: candidate for candidate in candidates}
        if decision == "add" and isinstance(selected_ids, list):
            selected = [by_id.get(selected_id) for selected_id in selected_ids]
            selected_candidates = [candidate for candidate in selected if candidate is not None]
            if len(selected_candidates) != len(selected) or (
                any(candidate.get("in_collection") for candidate in selected_candidates)
                and not all(candidate.get("in_collection") for candidate in selected_candidates)
            ):
                decision = "stale"
            elif selected_candidates and all(
                candidate.get("in_collection") for candidate in selected_candidates
            ):
                decision = "already_collected"
        items.append(
            {
                **row,
                "audit_status": status,
                "decision": decision,
                "selected_trakt_ids": selected_ids,
                "review_note": old.get("review_note", ""),
                "candidates": candidates,
            }
        )
        if progress_path:
            _write_json(progress_path, {"schema_version": 1, "items": items})
    return {"schema_version": 1, "items": items}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON through a temporary file so readers never see a truncated document."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def partial_path(json_path: Path) -> Path:
    """Return the in-progress audit path, kept apart from the complete decision file."""
    return json_path.with_name(json_path.name + ".partial")


def write_markdown(path: Path, data: dict[str, Any]) -> None:
    lines = [
        "# Amazon to Trakt library audit",
        "",
        "Set `decision`, `selected_trakt_ids`, and `review_note` in the JSON review file; "
        "this Markdown is a generated view.",
        "",
    ]
    for index, item in enumerate(data["items"], start=1):
        lines.extend(
            [
                f"## {index}. {item['amazon_title']}",
                "",
                f"- Status: `{item['audit_status']}` | decision: `{item['decision']}`",
                f"- Amazon: [{item['amazon_url']}]({item['amazon_url']})",
                f"- Selected Trakt IDs: `{item.get('selected_trakt_ids') or []}`",
                f"- Review note: {item.get('review_note') or ''}",
            ]
        )
        if not item["candidates"]:
            lines.append("- Trakt candidates: none")
        else:
            lines.append("- Trakt candidates:")
            for candidate in item["candidates"]:
                collected = "already collected" if candidate["in_collection"] else "not collected"
                overview = candidate["overview"].replace("\n", " ").strip()
                candidate_line = (
                    f"  - [{candidate['trakt_title']} ({candidate['trakt_year']})]"
                    f"({candidate['trakt_url']}) — ID `{candidate['trakt_id']}`, "
                    f"{collected}, similarity `{candidate['title_similarity']}`"
                    f"{', exact watchlist cache match' if candidate.get('cache_match') else ''}"
                )
                lines.append(candidate_line)
                if overview:
                    lines.append(f"    - {overview}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


# pylint: disable=too-many-locals,too-many-branches
def export_reviewed(path: Path, output: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_decisions(data)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen_ids: set[int] = set()
    for item in data.get("items", []):
        if item.get("decision") == "stale":
            skipped.append(
                f"{item.get('amazon_title', 'unknown item')} (marked stale; needs re-review)"
            )
            continue
        if item.get("decision") != "add":
            continue
        title = str(item.get("amazon_title", "unknown item"))
        selected_ids = item.get("selected_trakt_ids", [])
        if not isinstance(selected_ids, list) or not selected_ids:
            raise ValueError(f"No selected Trakt IDs for {title}.")
        if not str(item.get("review_note", "")).strip():
            raise ValueError(f"Add a review_note with match evidence for {title}.")
        invalid = False
        item_rows: list[dict[str, Any]] = []
        for selected_id in selected_ids:
            candidate = next(
                (c for c in item.get("candidates", []) if c.get("trakt_id") == selected_id), None
            )
            if candidate is None:
                skipped.append(
                    f"{title} (selected Trakt ID {selected_id} is no longer a candidate)"
                )
                invalid = True
                continue
            if candidate.get("in_collection"):
                skipped.append(
                    f"{title} (selected movie already collected: {candidate.get('trakt_title')})"
                )
                invalid = True
                continue
            year = candidate.get("trakt_year")
            slug = str(candidate.get("trakt_slug", "")).strip()
            if not isinstance(year, int) or not slug:
                skipped.append(
                    f"{title} (candidate {selected_id} has missing/invalid year or slug)"
                )
                invalid = True
                continue
            if selected_id in seen_ids or any(row["trakt_id"] == selected_id for row in item_rows):
                raise ValueError(f"Duplicate selected Trakt ID: {selected_id}.")
            item_rows.append(
                {
                    "amazon_title": item["amazon_title"],
                    "amazon_url": item["amazon_url"],
                    "trakt_title": candidate["trakt_title"],
                    "trakt_year": year,
                    "trakt_id": selected_id,
                    "trakt_slug": slug,
                }
            )
        if not invalid:
            for row in item_rows:
                seen_ids.add(row["trakt_id"])
            rows.extend(item_rows)
    if skipped:
        print("Skipped reviewed items: " + "; ".join(skipped), file=sys.stderr)
    if not rows:
        if skipped:
            raise ValueError("No valid reviewed entries to export; skipped: " + "; ".join(skipped))
        raise ValueError("No entries are marked decision='add' with a selected Trakt candidate.")
    with output.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main() -> None:
    args = parse_args()
    try:
        if args.export_reviewed:
            if args.output is None:
                raise ValueError("--output is required with --export-reviewed.")
            print(f"Exported {export_reviewed(args.export_reviewed, args.output)} reviewed movies.")
            return
        if args.json is None or args.markdown is None:
            raise ValueError("--json and --markdown are required with --input.")
        if args.previous and args.json.resolve() == args.previous.resolve():
            shutil.copy2(args.previous, args.previous.with_suffix(args.previous.suffix + ".bak"))
        progress = partial_path(args.json)
        audit = build_audit(
            load_amazon_rows(args.input), args.limit, previous_decisions(args.previous), progress
        )
        _write_json(args.json, audit)
        progress.unlink(missing_ok=True)
        write_markdown(args.markdown, audit)
        print(f"Audited {len(audit['items'])} Amazon entries; nothing was added to Trakt.")
        print(f"Review files: {args.json} and {args.markdown}")
    except (
        OSError,
        sqlite3.Error,
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
    ) as exc:
        print(f"Audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

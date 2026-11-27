"""Match an Amazon movie-library export to Trakt and prepare a review report."""

# Project modules are imported after adding the checkout to sys.path.
# ruff: noqa: E402
# pylint: disable=wrong-import-position

import argparse
import csv
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent))

from trakt_watchlist_monitor import db, trakt
from trakt_watchlist_monitor.config import settings

COLLECTION_WORDS = re.compile(r"\b(collection|triple feature|bundle|2-film|3-film|4-film)\b", re.I)


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
    return parser.parse_args(argv)


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"\([^)]*(?:4k|uhd|hd|bonus|x-ray|edition)[^)]*\)", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


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
            if not title or not url:
                raise ValueError(f"Missing title or Amazon URL at CSV line {line_number}.")
            if url in seen:
                continue
            seen.add(url)
            rows.append({"amazon_title": title, "amazon_url": url})
    if not rows:
        raise ValueError("Amazon CSV contains no library entries.")
    return rows


def previous_decisions(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["amazon_url"]: row
        for row in data.get("items", [])
        if isinstance(row, dict) and isinstance(row.get("amazon_url"), str)
    }


def search_candidates(
    title: str, limit: int, cached_movies: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    response = trakt._request_with_refresh(  # pylint: disable=protected-access
        "GET", f"/search/movie?query={quote(title)}&limit={limit}"
    )
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
                "title_similarity": round(
                    SequenceMatcher(
                        None, normalize_title(title), normalize_title(candidate_title)
                    ).ratio(),
                    3,
                ),
            }
        )
    candidate_ids = {candidate["trakt_id"] for candidate in candidates}
    normalized_title = normalize_title(title)
    for cached in cached_movies or []:
        if (
            normalize_title(str(cached.get("trakt_title", ""))) != normalized_title
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


def collect_ids() -> set[int]:
    collection = trakt._get(  # pylint: disable=protected-access
        f"/users/{settings.trakt_username}/collection/movies"
    )
    return {
        trakt_id
        for item in collection
        if isinstance((trakt_id := trakt._item_trakt_id(item)), int)  # pylint: disable=protected-access
    }


def classify_status(title: str, candidates: list[dict[str, Any]]) -> str:
    exact = [
        candidate
        for candidate in candidates
        if normalize_title(candidate["trakt_title"]) == normalize_title(title)
    ]
    if any(candidate["in_collection"] for candidate in exact):
        return "already_collected"
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


def build_audit(
    rows: list[dict[str, str]], limit: int, previous: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    collected = collect_ids()
    conn = db.init_db(settings.db_path)
    try:
        cached_movies = db.get_current_watchlist_movies(conn)
    finally:
        conn.close()
    items: list[dict[str, Any]] = []
    for row in rows:
        old = previous.get(row["amazon_url"], {})
        candidates = search_candidates(row["amazon_title"], limit, cached_movies)
        for candidate in candidates:
            candidate["in_collection"] = candidate["trakt_id"] in collected
        status = classify_status(row["amazon_title"], candidates)
        items.append(
            {
                **row,
                "audit_status": status,
                "decision": old.get("decision", "pending"),
                "selected_trakt_ids": old.get("selected_trakt_ids", []),
                "review_note": old.get("review_note", ""),
                "candidates": candidates,
            }
        )
    return {"schema_version": 1, "items": items}


def write_markdown(path: Path, data: dict[str, Any]) -> None:
    lines = [
        "# Amazon to Trakt library audit",
        "",
        "Set `decision` and `selected_trakt_ids` in the JSON review file; "
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


def export_reviewed(path: Path, output: Path) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for item in data.get("items", []):
        if item.get("decision") != "add":
            continue
        selected_ids = item.get("selected_trakt_ids", [])
        if not isinstance(selected_ids, list) or not selected_ids:
            raise ValueError(f"No selected Trakt IDs for {item.get('amazon_title')}.")
        if not str(item.get("review_note", "")).strip():
            raise ValueError(
                f"Add a review_note with match evidence for {item.get('amazon_title')}."
            )
        for selected_id in selected_ids:
            candidate = next(
                (c for c in item.get("candidates", []) if c.get("trakt_id") == selected_id), None
            )
            if candidate is None:
                raise ValueError(
                    f"Selected Trakt ID is not a candidate for {item.get('amazon_title')}."
                )
            if candidate.get("in_collection"):
                raise ValueError(
                    f"Selected movie is already collected: {candidate.get('trakt_title')}."
                )
            if selected_id in seen_ids:
                raise ValueError(f"Duplicate selected Trakt ID: {selected_id}.")
            seen_ids.add(selected_id)
            rows.append(
                {
                    "amazon_title": item["amazon_title"],
                    "amazon_url": item["amazon_url"],
                    "trakt_title": candidate["trakt_title"],
                    "trakt_year": candidate["trakt_year"],
                    "trakt_id": selected_id,
                    "trakt_slug": candidate["trakt_slug"],
                }
            )
    if not rows:
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
        audit = build_audit(
            load_amazon_rows(args.input), args.limit, previous_decisions(args.previous)
        )
        args.json.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        write_markdown(args.markdown, audit)
        print(f"Audited {len(audit['items'])} Amazon entries; nothing was added to Trakt.")
        print(f"Review files: {args.json} and {args.markdown}")
    except (OSError, requests.RequestException, ValueError, KeyError) as exc:
        print(f"Audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

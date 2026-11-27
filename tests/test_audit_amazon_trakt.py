import csv
import json

import pytest
from scripts.audit_amazon_trakt import (
    classify_status,
    export_reviewed,
    load_amazon_rows,
    normalize_title,
    search_candidates,
)
from trakt_watchlist_monitor import trakt


class FakeResponse:
    def json(self):
        return []


def test_load_amazon_export_headers(tmp_path):
    path = tmp_path / "amazon.csv"
    path.write_text(
        "Amazon title,Amazon URL\nForbidden Kingdom,https://amazon.test/item\n",
        encoding="utf-8",
    )

    assert load_amazon_rows(path) == [
        {"amazon_title": "Forbidden Kingdom", "amazon_url": "https://amazon.test/item"}
    ]


def test_normalize_title_strips_format_tags():
    assert normalize_title("Robin Hood (2018) (4K UHD)") == "robin hood 2018"


def test_search_candidates_adds_exact_watchlist_cache_match(monkeypatch):
    monkeypatch.setattr(trakt, "_request_with_refresh", lambda *args, **kwargs: FakeResponse())
    cached = [
        {
            "trakt_id": 438631,
            "trakt_title": "Dune",
            "trakt_year": 2021,
            "trakt_slug": "dune-2021",
            "tmdb_id": 438631,
            "imdb_id": "tt1160419",
        }
    ]

    candidates = search_candidates("Dune", 10, cached)

    assert candidates == [
        {
            **cached[0],
            "trakt_url": "https://trakt.tv/movies/dune-2021",
            "overview": "",
            "search_score": None,
            "title_similarity": 1.0,
            "cache_match": True,
        }
    ]


def test_search_candidates_does_not_use_fuzzy_cache_match(monkeypatch):
    monkeypatch.setattr(trakt, "_request_with_refresh", lambda *args, **kwargs: FakeResponse())
    cached = [{"trakt_id": 1, "trakt_title": "Dune: Part Two", "trakt_year": 2024}]

    assert search_candidates("Dune", 10, cached) == []


def test_classify_near_title_match_already_in_collection():
    candidates = [
        {"trakt_title": "The Forbidden Kingdom", "in_collection": True, "title_similarity": 0.96}
    ]

    assert classify_status("Forbidden Kingdom", candidates) == "collected_candidate_review"


def test_export_reviewed_only_exports_confirmed_missing_candidate(tmp_path):
    audit_path = tmp_path / "review.json"
    output_path = tmp_path / "add.csv"
    audit_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "amazon_title": "Forbidden Kingdom",
                        "amazon_url": "https://amazon.test/item",
                        "decision": "add",
                        "selected_trakt_ids": [1159],
                        "review_note": "Amazon page and synopsis match the 2008 Trakt movie.",
                        "candidates": [
                            {
                                "trakt_id": 1159,
                                "trakt_title": "The Forbidden Kingdom",
                                "trakt_year": 2008,
                                "trakt_slug": "the-forbidden-kingdom-2008",
                                "in_collection": False,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert export_reviewed(audit_path, output_path) == 1
    with output_path.open(encoding="utf-8", newline="") as file:
        row = next(csv.DictReader(file))
    assert row["trakt_id"] == "1159"
    assert row["trakt_title"] == "The Forbidden Kingdom"


def test_export_reviewed_rejects_already_collected_candidate(tmp_path):
    audit_path = tmp_path / "review.json"
    audit_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "amazon_title": "Forbidden Kingdom",
                        "decision": "add",
                        "selected_trakt_ids": [1159],
                        "review_note": "Matched Amazon page to the Trakt record.",
                        "candidates": [{"trakt_id": 1159, "in_collection": True}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="already collected"):
        export_reviewed(audit_path, tmp_path / "add.csv")

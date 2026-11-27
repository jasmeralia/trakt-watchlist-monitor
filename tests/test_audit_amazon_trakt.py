import csv
import json

import pytest
import requests
from scripts.audit_amazon_trakt import (
    classify_status,
    export_reviewed,
    load_amazon_rows,
    normalize_title,
    previous_decisions,
    search_candidates,
    title_similarity,
    title_year,
    write_markdown,
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


def test_remake_collected_status_requires_matching_year():
    candidates = [
        {
            "trakt_id": 1,
            "trakt_title": "Total Recall",
            "trakt_year": 1990,
            "in_collection": True,
            "title_similarity": 1.0,
        },
        {
            "trakt_id": 2,
            "trakt_title": "Total Recall",
            "trakt_year": 2012,
            "in_collection": False,
            "title_similarity": 1.0,
        },
    ]
    assert classify_status("Total Recall (2012)", candidates) == "title_match_collected_review"
    assert classify_status("Total Recall (1990)", candidates) == "already_collected"


def test_exact_collected_title_without_year_requires_review():
    assert classify_status(
        "Total Recall", [{"trakt_title": "Total Recall", "in_collection": True}]
    ) == ("title_match_collected_review")


def test_unicode_normalization_never_matches_empty_strings():
    assert normalize_title("千と千尋の神隠し") == "千と千尋の神隠し"
    assert normalize_title("Сталкер") == "сталкер"
    assert title_similarity("千と千尋の神隠し", "Сталкер") == 0.0


def test_format_tags_use_word_boundaries_and_extract_year():
    assert normalize_title("Kon-Tiki (The Expedition)") == "kon tiki the expedition"
    assert normalize_title("Kon-Tiki (4K UHD)") == "kon tiki"
    assert title_year("Total Recall (2012) 4K") == 2012


def test_cache_does_not_match_empty_unicode_normalization(monkeypatch):
    monkeypatch.setattr(trakt, "_request_with_refresh", lambda *args, **kwargs: FakeResponse())
    cached = [{"trakt_id": 3, "trakt_title": "Сталкер"}]

    assert search_candidates("", 10, cached) == []
    assert search_candidates("千と千尋の神隠し", 10, cached) == []
    assert classify_status(
        "千と千尋の神隠し",
        [{"trakt_title": "Сталкер", "in_collection": True, "title_similarity": 0.0}],
    ) == ("ambiguous_review")


def test_amazon_csv_bom_blank_rows_and_canonical_duplicates(tmp_path):
    path = tmp_path / "amazon.csv"
    path.write_text(
        "\ufeffAmazon title,Amazon URL\n,,\nDune,https://amazon.test/dp/B012345678?ref_=x\n"
        "Dune duplicate,https://amazon.test/gp/video/detail/B012345678\n",
        encoding="utf-8",
    )
    assert load_amazon_rows(path) == [
        {"amazon_title": "Dune", "amazon_url": "https://amazon.test/dp/B012345678?ref_=x"}
    ]


def test_amazon_csv_missing_columns_is_clear(tmp_path):
    path = tmp_path / "amazon.csv"
    path.write_text("Name,Link\nDune,url\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs Amazon title and Amazon URL"):
        load_amazon_rows(path)


def test_previous_matches_canonical_url_and_validates_decision(tmp_path):
    path = tmp_path / "previous.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "amazon_title": "Dune",
                        "amazon_url": "https://amazon.test/gp/video/detail/B012345678",
                        "decision": "add",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert previous_decisions(path)["B012345678"]["decision"] == "add"
    path.write_text(
        json.dumps({"items": [{"amazon_title": "Dune", "decision": "Add"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Invalid decision 'Add'.*Dune"):
        previous_decisions(path)


def test_export_rejects_invalid_decision_value(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps({"items": [{"amazon_title": "Dune", "decision": "ad"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Invalid decision 'ad'.*Dune"):
        export_reviewed(path, tmp_path / "out.csv")


def test_markdown_instructions_include_review_note(tmp_path):
    path = tmp_path / "review.md"
    write_markdown(path, {"items": []})
    assert "`review_note`" in path.read_text(encoding="utf-8")


def test_export_skips_stale_collected_and_invalid_candidate_but_exports_valid(tmp_path, capsys):
    path, output = tmp_path / "review.json", tmp_path / "out.csv"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "amazon_title": "Collected",
                        "amazon_url": "u1",
                        "decision": "add",
                        "selected_trakt_ids": [1],
                        "review_note": "verified",
                        "candidates": [
                            {
                                "trakt_id": 1,
                                "trakt_title": "A",
                                "trakt_year": 2000,
                                "trakt_slug": "a",
                                "in_collection": True,
                            }
                        ],
                    },
                    {
                        "amazon_title": "Bad metadata",
                        "amazon_url": "u2",
                        "decision": "add",
                        "selected_trakt_ids": [2],
                        "review_note": "verified",
                        "candidates": [
                            {
                                "trakt_id": 2,
                                "trakt_title": "B",
                                "trakt_year": None,
                                "trakt_slug": "",
                                "in_collection": False,
                            }
                        ],
                    },
                    {
                        "amazon_title": "Valid",
                        "amazon_url": "u3",
                        "decision": "add",
                        "selected_trakt_ids": [3],
                        "review_note": "verified",
                        "candidates": [
                            {
                                "trakt_id": 3,
                                "trakt_title": "C",
                                "trakt_year": 2001,
                                "trakt_slug": "c",
                                "in_collection": False,
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert export_reviewed(path, output) == 1
    err = capsys.readouterr().err
    assert "Collected" in err and "Bad metadata" in err


def test_build_audit_reads_database_read_only_and_carries_decisions(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        audit,
        "search_candidates",
        lambda *args, **kwargs: [
            {
                "trakt_id": 10,
                "trakt_title": "Dune",
                "trakt_year": 2021,
                "trakt_slug": "dune",
                "in_collection": False,
            }
        ],
    )
    db_path = tmp_path / "monitor.sqlite"
    monkeypatch.setattr(audit.settings, "db_path", str(db_path))
    rows = [{"amazon_title": "Dune", "amazon_url": "https://amazon.test/dp/B012345678"}]
    previous = {"B012345678": {"decision": "add", "selected_trakt_ids": [10], "review_note": "yes"}}
    data = audit.build_audit(rows, 10, previous)
    assert not db_path.exists()
    assert data["items"][0]["decision"] == "add"
    assert data["items"][0]["review_note"] == "yes"


def test_build_audit_marks_missing_previous_candidate_stale(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit, "search_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "missing.sqlite"))
    item = audit.build_audit(
        [{"amazon_title": "Dune", "amazon_url": "https://amazon.test/item"}],
        10,
        {"https://amazon.test/item": {"decision": "add", "selected_trakt_ids": [5]}},
    )["items"][0]
    assert item["decision"] == "stale"


def test_retry_search_honors_retry_after(monkeypatch):
    from scripts import audit_amazon_trakt as audit

    response = type("Response", (), {"status_code": 429, "headers": {"Retry-After": "3"}})()
    calls = iter([requests.HTTPError(response=response), FakeResponse()])

    def request(*args, **kwargs):
        result = next(calls)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(audit.trakt, "_request_with_refresh", request)
    delays = []
    monkeypatch.setattr(audit.time, "sleep", delays.append)
    assert audit._search_response("Dune", 10).__class__ is FakeResponse
    assert delays == [3.0]


def test_build_audit_records_item_error_and_progress(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        audit,
        "search_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(AttributeError("bad payload")),
    )
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "missing.sqlite"))
    progress = tmp_path / "progress.json"
    data = audit.build_audit([{"amazon_title": "Dune", "amazon_url": "u"}], 10, {}, progress)
    assert data["items"][0]["audit_status"] == "error"
    assert json.loads(progress.read_text()) == data


def test_build_audit_marks_fully_collected_carried_add_complete(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    monkeypatch.setattr(
        audit.trakt, "_get", lambda *args, **kwargs: [{"movie": {"ids": {"trakt": 10}}}]
    )
    monkeypatch.setattr(
        audit,
        "search_candidates",
        lambda *args, **kwargs: [
            {
                "trakt_id": 10,
                "trakt_title": "Dune",
                "trakt_year": 2021,
                "trakt_slug": "dune",
                "in_collection": False,
            }
        ],
    )
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "missing.sqlite"))
    item = audit.build_audit(
        [{"amazon_title": "Dune", "amazon_url": "u"}],
        10,
        {"u": {"decision": "add", "selected_trakt_ids": [10], "review_note": "verified"}},
    )["items"][0]
    assert item["decision"] == "already_collected"
    assert item["candidates"][0]["in_collection"] is True


def test_export_supports_multi_id_and_rejects_empty_note_or_non_list_ids(tmp_path):
    path, output = tmp_path / "review.json", tmp_path / "out.csv"
    base = {
        "amazon_title": "Bundle",
        "amazon_url": "u",
        "decision": "add",
        "review_note": "checked",
        "candidates": [
            {"trakt_id": 1, "trakt_title": "A", "trakt_year": 2000, "trakt_slug": "a"},
            {"trakt_id": 2, "trakt_title": "B", "trakt_year": 2001, "trakt_slug": "b"},
        ],
    }
    path.write_text(
        json.dumps({"items": [{**base, "selected_trakt_ids": [1, 2]}]}), encoding="utf-8"
    )
    assert export_reviewed(path, output) == 2
    path.write_text(json.dumps({"items": [{**base, "selected_trakt_ids": "1"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="No selected Trakt IDs"):
        export_reviewed(path, output)
    path.write_text(
        json.dumps({"items": [{**base, "selected_trakt_ids": [1], "review_note": " "}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="review_note"):
        export_reviewed(path, output)


def test_export_rejects_duplicate_ids_across_items(tmp_path):
    path, output = tmp_path / "review.json", tmp_path / "out.csv"
    item = {
        "amazon_title": "Movie",
        "amazon_url": "https://amazon.test/movie",
        "decision": "add",
        "selected_trakt_ids": [7],
        "review_note": "verified",
        "candidates": [
            {"trakt_id": 7, "trakt_title": "Movie", "trakt_year": 2020, "trakt_slug": "movie"}
        ],
    }
    path.write_text(
        json.dumps({"items": [item, {**item, "amazon_title": "Duplicate"}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Duplicate selected Trakt ID: 7"):
        export_reviewed(path, output)


def test_export_skips_selected_id_missing_from_fresh_candidates(tmp_path, capsys):
    path, output = tmp_path / "review.json", tmp_path / "out.csv"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "amazon_title": "Stale",
                        "decision": "add",
                        "selected_trakt_ids": [99],
                        "review_note": "verified",
                        "candidates": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="No valid reviewed entries"):
        export_reviewed(path, output)
    assert "no longer a candidate" in capsys.readouterr().err


def test_export_reports_carried_stale_item_and_exports_valid_items(tmp_path, capsys):
    path, output = tmp_path / "review.json", tmp_path / "out.csv"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"amazon_title": "Stale", "decision": "stale", "selected_trakt_ids": [8]},
                    {
                        "amazon_title": "Valid",
                        "amazon_url": "u",
                        "decision": "add",
                        "selected_trakt_ids": [9],
                        "review_note": "verified",
                        "candidates": [
                            {
                                "trakt_id": 9,
                                "trakt_title": "Movie",
                                "trakt_year": 2020,
                                "trakt_slug": "movie",
                                "in_collection": False,
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert export_reviewed(path, output) == 1
    assert "Stale" in capsys.readouterr().err


def test_main_requires_json_and_markdown_outputs(monkeypatch, capsys):
    from scripts import audit_amazon_trakt as audit

    monkeypatch.setattr(
        audit,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "export_reviewed": None,
                "input": None,
                "json": None,
                "markdown": None,
                "previous": None,
                "limit": 10,
            },
        )(),
    )
    with pytest.raises(SystemExit) as error:
        audit.main()
    assert error.value.code == 1
    assert "Audit failed: ValueError" in capsys.readouterr().err


def test_main_backups_previous_json_before_incremental_overwrite(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    input_path = tmp_path / "amazon.csv"
    input_path.write_text(
        "Amazon title,Amazon URL\nDune,https://amazon.test/item\n", encoding="utf-8"
    )
    json_path = tmp_path / "audit.json"
    original = json.dumps({"items": [{"amazon_title": "prior"}]})
    json_path.write_text(original, encoding="utf-8")
    markdown_path = tmp_path / "audit.md"
    monkeypatch.setattr(
        audit,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "export_reviewed": None,
                "input": input_path,
                "json": json_path,
                "markdown": markdown_path,
                "previous": json_path,
                "limit": 10,
                "output": None,
            },
        )(),
    )
    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit, "search_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "missing.sqlite"))

    audit.main()

    assert json_path.with_suffix(".json.bak").read_text(encoding="utf-8") == original
    assert json.loads(json_path.read_text(encoding="utf-8"))["items"][0]["decision"] == "pending"


def test_parse_args_rejects_nonpositive_limit():
    from scripts.audit_amazon_trakt import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--input", "in.csv", "--limit", "0"])


def _main_args(input_path, json_path, markdown_path, previous):
    return lambda: type(
        "Args",
        (),
        {
            "export_reviewed": None,
            "input": input_path,
            "json": json_path,
            "markdown": markdown_path,
            "previous": previous,
            "limit": 10,
            "output": None,
        },
    )()


def test_main_crash_keeps_reviewed_json_and_backup_complete(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    input_path = tmp_path / "amazon.csv"
    input_path.write_text(
        "Amazon title,Amazon URL\nDune,https://amazon.test/dp/B000000001\n"
        "Arrival,https://amazon.test/dp/B000000002\n",
        encoding="utf-8",
    )
    json_path, markdown_path = tmp_path / "audit.json", tmp_path / "audit.md"
    reviewed = {
        "items": [
            {
                "amazon_title": title,
                "amazon_url": f"https://amazon.test/dp/{asin}",
                "decision": "no_match",
                "selected_trakt_ids": [],
                "review_note": f"checked {title}",
            }
            for title, asin in (("Dune", "B000000001"), ("Arrival", "B000000002"))
        ]
    }
    original = json.dumps(reviewed)
    json_path.write_text(original, encoding="utf-8")
    args = _main_args(input_path, json_path, markdown_path, json_path)
    monkeypatch.setattr(audit, "parse_args", args)
    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "missing.sqlite"))
    calls = []

    def crash_on_second(title, *args, **kwargs):
        calls.append(title)
        if len(calls) == 2:
            raise RuntimeError("interrupted")
        return []

    monkeypatch.setattr(audit, "search_candidates", crash_on_second)
    with pytest.raises(RuntimeError, match="interrupted"):
        audit.main()

    assert json_path.read_text(encoding="utf-8") == original
    partial = json.loads(audit.partial_path(json_path).read_text(encoding="utf-8"))
    assert [item["amazon_title"] for item in partial["items"]] == ["Dune"]

    monkeypatch.setattr(audit, "search_candidates", lambda *args, **kwargs: [])
    audit.main()

    backup = json_path.with_suffix(".json.bak")
    assert backup.read_text(encoding="utf-8") == original
    final = json.loads(json_path.read_text(encoding="utf-8"))
    assert [item["review_note"] for item in final["items"]] == ["checked Dune", "checked Arrival"]
    assert not audit.partial_path(json_path).exists()


def test_build_audit_search_error_preserves_reviewed_decision(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit

    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        audit,
        "search_candidates",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.ConnectionError("offline")),
    )
    monkeypatch.setattr(audit.settings, "db_path", str(tmp_path / "missing.sqlite"))
    previous = {
        "u": {"decision": "add", "selected_trakt_ids": [10], "review_note": "synopsis matches"}
    }

    rows = [{"amazon_title": "Dune", "amazon_url": "u"}]
    item = audit.build_audit(rows, 10, previous)["items"][0]

    assert item["audit_status"] == "error"
    assert item["decision"] == "add"
    assert item["selected_trakt_ids"] == [10]
    assert item["review_note"] == "synopsis matches"


def test_build_audit_uses_monitor_cache_without_modifying_database(tmp_path, monkeypatch):
    from scripts import audit_amazon_trakt as audit
    from trakt_watchlist_monitor import db

    db_path = tmp_path / "prices.db"
    conn = db.init_db(str(db_path))
    db.sync_watchlist_items(
        conn,
        [
            {
                "trakt_id": 438631,
                "media_type": "movie",
                "title": "Dune",
                "year": 2021,
                "trakt_slug": "dune-2021",
                "in_collection": False,
            }
        ],
    )
    conn.close()
    before = db_path.read_bytes()
    monkeypatch.setattr(audit.settings, "db_path", str(db_path))
    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(trakt, "_request_with_refresh", lambda *args, **kwargs: FakeResponse())

    item = audit.build_audit(
        [{"amazon_title": "Dune", "amazon_url": "https://amazon.test/dp/B000000001"}], 10, {}
    )["items"][0]

    assert [candidate["trakt_id"] for candidate in item["candidates"]] == [438631]
    assert item["candidates"][0]["cache_match"] is True
    assert item["audit_status"] == "exact_candidate_review"
    assert db_path.read_bytes() == before


def test_build_audit_warns_when_database_lacks_watchlist_table(tmp_path, monkeypatch, capsys):
    import sqlite3

    from scripts import audit_amazon_trakt as audit

    db_path = tmp_path / "prices.db"
    sqlite3.connect(db_path).close()
    monkeypatch.setattr(audit.settings, "db_path", str(db_path))
    monkeypatch.setattr(audit.trakt, "_get", lambda *args, **kwargs: [])
    monkeypatch.setattr(audit, "search_candidates", lambda *args, **kwargs: [])

    audit.build_audit([{"amazon_title": "Dune", "amazon_url": "u"}], 10, {})

    assert "no watchlist_items table" in capsys.readouterr().err

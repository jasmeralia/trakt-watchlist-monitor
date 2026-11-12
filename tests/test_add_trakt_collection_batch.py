import csv

import pytest
from scripts import add_trakt_collection_batch as batch


def write_csv(path, rows):
    fields = [
        "amazon_title",
        "amazon_url",
        "trakt_title",
        "trakt_year",
        "trakt_id",
        "trakt_slug",
    ]
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_load_targets_reads_reviewed_movie_csv(tmp_path):
    path = tmp_path / "movies.csv"
    write_csv(
        path,
        [
            {
                "amazon_title": "Arrival",
                "amazon_url": "https://amazon.test/arrival",
                "trakt_title": "Arrival",
                "trakt_year": "2016",
                "trakt_id": "210803",
                "trakt_slug": "arrival-2016",
            }
        ],
    )

    target = batch.load_targets(path)[0]

    assert target.label == "Arrival (2016)"
    assert target.trakt_id == 210803


def test_load_targets_rejects_duplicate_trakt_ids(tmp_path):
    path = tmp_path / "movies.csv"
    row = {
        "amazon_title": "Arrival",
        "amazon_url": "https://amazon.test/arrival",
        "trakt_title": "Arrival",
        "trakt_year": "2016",
        "trakt_id": "210803",
        "trakt_slug": "arrival-2016",
    }
    write_csv(path, [row, row])

    with pytest.raises(ValueError, match="Duplicate Trakt movie ID"):
        batch.load_targets(path)


def test_build_payload_uses_power_shell_defaults():
    target = batch.MovieTarget(
        amazon_title="Arrival",
        amazon_url="https://amazon.test/arrival",
        trakt_title="Arrival",
        trakt_year=2016,
        trakt_id=210803,
        trakt_slug="arrival-2016",
    )

    payload = batch.build_payload(
        [target], media_type="digital", resolution="hd_720p", audio_channels="2.0"
    )

    assert len(payload["movies"]) == 1
    assert payload["movies"][0]["ids"] == {"trakt": 210803}
    assert payload["movies"][0]["media_type"] == "digital"
    assert payload["movies"][0]["resolution"] == "hd_720p"
    assert payload["movies"][0]["audio_channels"] == "2.0"

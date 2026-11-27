import csv

import pytest
import requests
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


def test_build_payload_includes_uhd_resolution():
    target = batch.MovieTarget(
        amazon_title="Arrival",
        amazon_url="https://amazon.test/arrival",
        trakt_title="Arrival",
        trakt_year=2016,
        trakt_id=210803,
        trakt_slug="arrival-2016",
    )

    payload = batch.build_payload(
        [target], media_type="digital", resolution="uhd_4k", audio_channels="2.0"
    )

    assert len(payload["movies"]) == 1
    assert payload["movies"][0]["ids"] == {"trakt": 210803}
    assert payload["movies"][0]["media_type"] == "digital"
    assert payload["movies"][0]["resolution"] == "uhd_4k"
    assert payload["movies"][0]["audio_channels"] == "2.0"


def test_parse_args_defaults_to_uhd_resolution(tmp_path):
    args = batch.parse_args(["--input", str(tmp_path / "movies.csv")])

    assert args.resolution == "uhd_4k"


def movie_row(index, **overrides):
    row = {
        "amazon_title": f"Movie {index}",
        "amazon_url": f"https://amazon.test/{index}",
        "trakt_title": f"Movie {index}",
        "trakt_year": "2000",
        "trakt_id": str(index),
        "trakt_slug": f"movie-{index}",
    }
    return {**row, **overrides}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class FakeTrakt:
    """Serve movie lookups and collection state; collection grows only when POSTed to."""

    def __init__(self, collected=(), movies=None, drop_on_post=()):
        self.collected = set(collected)
        self.movies = movies or {}
        self.drop_on_post = set(drop_on_post)
        self.posts = []
        self.collection_reads = 0

    def request(self, method, path, params=None, json=None):
        if method == "POST":
            assert path == "/sync/collection"
            self.posts.append(json)
            ids = {movie["ids"]["trakt"] for movie in json["movies"]}
            self.collected |= ids - self.drop_on_post
            return FakeResponse({"added": {"movies": len(ids - self.drop_on_post)}})
        trakt_id = int(path.split("/")[2].split("?")[0])
        default = {
            "title": f"Movie {trakt_id}",
            "year": 2000,
            "ids": {"trakt": trakt_id, "slug": f"movie-{trakt_id}"},
        }
        return FakeResponse(self.movies.get(trakt_id, default))

    def get(self, path):
        assert path == "/users/username/collection/movies"
        self.collection_reads += 1
        return [{"movie": {"ids": {"trakt": trakt_id}}} for trakt_id in sorted(self.collected)]


@pytest.fixture
def fake_trakt(monkeypatch):
    def install(**kwargs):
        fake = FakeTrakt(**kwargs)
        monkeypatch.setattr(batch.trakt, "_request_with_refresh", fake.request)
        monkeypatch.setattr(batch.trakt, "_get", fake.get)
        return fake

    return install


def batch_args(path, *extra):
    return batch.parse_args(["--input", str(path), *extra])


def test_parse_args_default_metadata(tmp_path):
    args = batch_args(tmp_path / "movies.csv")

    assert (args.media_type, args.resolution, args.audio_channels) == ("digital", "uhd_4k", "2.0")
    assert not args.commit and not args.force


def test_load_targets_accepts_bom_and_strips_values(tmp_path):
    path = tmp_path / "movies.csv"
    path.write_text(
        "﻿amazon_title,amazon_url,trakt_title,trakt_year,trakt_id,trakt_slug\n"
        " Arrival , https://amazon.test/a ,Arrival,2016,210803, arrival-2016 \n",
        encoding="utf-8",
    )

    target = batch.load_targets(path)[0]

    assert (target.amazon_title, target.amazon_url, target.trakt_slug) == (
        "Arrival",
        "https://amazon.test/a",
        "arrival-2016",
    )


def test_load_targets_names_missing_columns(tmp_path):
    path = tmp_path / "movies.csv"
    path.write_text("amazon_title,amazon_url,trakt_title\nA,u,A\n", encoding="utf-8")

    missing = "missing required columns: trakt_id, trakt_slug, trakt_year"
    with pytest.raises(ValueError, match=missing):
        batch.load_targets(path)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"trakt_year": ""}, "Invalid movie row at CSV line 2"),
        ({"trakt_id": "abc"}, "Invalid movie row at CSV line 2"),
        ({"trakt_slug": " "}, "Missing title, URL, or Trakt slug at CSV line 2"),
        ({"amazon_url": ""}, "Missing title, URL, or Trakt slug at CSV line 2"),
    ],
)
def test_load_targets_rejects_invalid_rows(tmp_path, overrides, message):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1, **overrides)])

    with pytest.raises(ValueError, match=message):
        batch.load_targets(path)


def test_load_targets_rejects_short_row(tmp_path):
    path = tmp_path / "movies.csv"
    path.write_text(
        "amazon_title,amazon_url,trakt_title,trakt_year,trakt_id,trakt_slug\nA,u\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid movie row at CSV line 2"):
        batch.load_targets(path)


def test_load_targets_rejects_empty_file(tmp_path):
    path = tmp_path / "movies.csv"
    write_csv(path, [])

    with pytest.raises(ValueError, match="contains no movies"):
        batch.load_targets(path)


@pytest.mark.parametrize(
    ("movie", "message"),
    [
        ({"title": "Movie 1", "year": 2000, "ids": {"trakt": 1, "slug": "other"}}, "disagree"),
        ({"title": "Movie 1", "year": 2000, "ids": {"trakt": 2, "slug": "movie-1"}}, "disagree"),
        ({"title": "Remake", "year": 2000, "ids": {"trakt": 1, "slug": "movie-1"}}, "changed"),
        ({"title": "Movie 1", "year": 2020, "ids": {"trakt": 1, "slug": "movie-1"}}, "changed"),
        ({"title": "Movie 1", "year": 2000}, "disagree"),
        (["not", "a", "movie"], "Unexpected Trakt response"),
    ],
)
def test_fetch_movie_verifies_id_slug_title_and_year(fake_trakt, movie, message):
    fake_trakt(movies={1: movie})
    target = batch.MovieTarget("Movie 1", "https://amazon.test/1", "Movie 1", 2000, 1, "movie-1")

    with pytest.raises(ValueError, match=message):
        batch.fetch_movie(target)


def test_fetch_collection_ids_ignores_items_without_ids(monkeypatch):
    monkeypatch.setattr(
        batch.trakt,
        "_get",
        lambda path: [{"movie": {"ids": {"trakt": 5}}}, {"movie": {"ids": {}}}, {}],
    )

    assert batch.fetch_collection_ids() == {5}


def test_run_refuses_to_overwrite_already_collected(tmp_path, fake_trakt):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1), movie_row(2)])
    fake = fake_trakt(collected={2})

    with pytest.raises(ValueError, match="already in the collection.*Movie 2 \\(2000\\)"):
        batch.run(batch_args(path, "--commit", "--force"))
    assert fake.posts == []


def test_run_verifies_every_movie_before_checking_collection(tmp_path, fake_trakt):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1), movie_row(2)])
    fake = fake_trakt(movies={2: {"title": "Movie 2", "year": 1999, "ids": {}}})

    with pytest.raises(ValueError, match="Movie 2"):
        batch.run(batch_args(path, "--commit", "--force"))
    assert fake.posts == [] and fake.collection_reads == 0


def test_run_preview_sends_nothing(tmp_path, fake_trakt, capsys):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1)])
    fake = fake_trakt()

    assert batch.run(batch_args(path)) is True

    assert fake.posts == []
    out = capsys.readouterr().out
    assert "Ready to add 1 movies (digital, uhd_4k)" in out and "Preview only" in out


def test_run_force_requires_commit(tmp_path):
    with pytest.raises(ValueError, match="--force requires --commit"):
        batch.run(batch_args(tmp_path / "movies.csv", "--force"))


@pytest.mark.parametrize("answer", ["n", "", "yes"])
def test_run_commit_cancels_unless_answer_is_y(tmp_path, fake_trakt, monkeypatch, answer):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1)])
    fake = fake_trakt()
    monkeypatch.setattr("builtins.input", lambda prompt: answer)

    assert batch.run(batch_args(path, "--commit")) is True
    assert fake.posts == []


def test_run_commit_treats_closed_stdin_as_cancel(tmp_path, fake_trakt, monkeypatch):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1)])
    fake = fake_trakt()

    def closed(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)

    assert batch.run(batch_args(path, "--commit")) is True
    assert fake.posts == []


def test_run_commit_with_confirmation_sends_default_metadata_and_verifies(
    tmp_path, fake_trakt, monkeypatch, capsys
):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1)])
    fake = fake_trakt()
    monkeypatch.setattr("builtins.input", lambda prompt: "Y")

    assert batch.run(batch_args(path, "--commit")) is True

    movie = fake.posts[0]["movies"][0]
    assert movie["ids"] == {"trakt": 1}
    assert (movie["media_type"], movie["resolution"], movie["audio_channels"]) == (
        "digital",
        "uhd_4k",
        "2.0",
    )
    assert movie["collected_at"].endswith(".000Z")
    out = capsys.readouterr().out
    assert "Trakt reported 1 movies added" in out and "Verified all 1 movies" in out


def test_run_force_batches_requests_of_at_most_100(tmp_path, fake_trakt, monkeypatch, capsys):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(index) for index in range(1, 251)])
    fake = fake_trakt()

    def no_prompt(prompt):
        raise AssertionError("--force must skip the prompt")

    monkeypatch.setattr("builtins.input", no_prompt)

    assert batch.run(batch_args(path, "--commit", "--force")) is True

    assert [len(post["movies"]) for post in fake.posts] == [100, 100, 50]
    sent = [movie["ids"]["trakt"] for post in fake.posts for movie in post["movies"]]
    assert sent == list(range(1, 251))
    out = capsys.readouterr().out
    assert "Sent batch 3 of 3 (50 movies)" in out and "Trakt reported 250 movies added" in out


def test_run_custom_metadata_is_sent(tmp_path, fake_trakt):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1)])
    fake = fake_trakt()

    batch.run(
        batch_args(
            path,
            "--commit",
            "--force",
            "--resolution",
            "hd_1080p",
            "--media-type",
            "bluray",
            "--audio-channels",
            "5.1",
        )
    )

    movie = fake.posts[0]["movies"][0]
    assert (movie["media_type"], movie["resolution"], movie["audio_channels"]) == (
        "bluray",
        "hd_1080p",
        "5.1",
    )


def test_run_reports_verification_failure(tmp_path, fake_trakt, capsys):
    path = tmp_path / "movies.csv"
    write_csv(path, [movie_row(1), movie_row(2)])
    fake_trakt(drop_on_post={2})

    assert batch.run(batch_args(path, "--commit", "--force")) is False

    assert "Verification failed for: Movie 2 (2000)" in capsys.readouterr().err


def test_send_batches_tolerates_unexpected_response_shape(monkeypatch):
    monkeypatch.setattr(
        batch.trakt, "_request_with_refresh", lambda *args, **kwargs: FakeResponse([])
    )

    assert batch.send_batches({"movies": [{"ids": {"trakt": 1}}]}) == 0


def run_main(monkeypatch, run_result=None, error=None):
    monkeypatch.setattr("sys.argv", ["add_trakt_collection_batch.py", "--input", "movies.csv"])

    def fake_run(args):
        if error is not None:
            raise error
        return run_result

    monkeypatch.setattr(batch, "run", fake_run)
    with pytest.raises(SystemExit) as exit_info:
        batch.main()
    return exit_info.value.code


@pytest.mark.parametrize(
    ("run_result", "error", "code"),
    [
        (True, None, 0),
        (False, None, 1),
        (None, requests.ConnectionError("offline"), 1),
        (None, ValueError("bad row"), 2),
        (None, FileNotFoundError("movies.csv"), 2),
    ],
)
def test_main_exit_codes(monkeypatch, capsys, run_result, error, code):
    assert run_main(monkeypatch, run_result, error) == code
    if error is not None:
        assert str(error) in capsys.readouterr().err

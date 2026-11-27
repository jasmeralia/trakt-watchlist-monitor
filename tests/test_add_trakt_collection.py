import pytest
import requests
from scripts import add_trakt_collection as single


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeTrakt:
    """Record Trakt calls and answer them from canned data; no network is used."""

    def __init__(self, item=None, movies=None, shows=None, post_result=None, after_post=None):
        self.item = item if item is not None else {"title": "Nobody", "year": 2021}
        self.movies = movies or []
        self.shows = shows or []
        self.post_result = post_result if post_result is not None else {"added": {"movies": 1}}
        self.after_post = after_post
        self.requests = []
        self.posted = []

    def request(self, method, path, params=None, json=None):
        self.requests.append((method, path))
        if method == "POST":
            self.posted.append(json)
            if self.after_post is not None:
                self.movies, self.shows = self.after_post
            return FakeResponse(self.post_result)
        return FakeResponse(self.item)

    def get(self, path):
        self.requests.append(("GET", path))
        return self.movies if "/movies" in path else self.shows


@pytest.fixture
def fake_trakt(monkeypatch):
    def install(**kwargs):
        fake = FakeTrakt(**kwargs)
        monkeypatch.setattr(single.trakt, "_request_with_refresh", fake.request)
        monkeypatch.setattr(single.trakt, "_get", fake.get)
        return fake

    return install


def movie_entry(slug, **metadata):
    return {
        "collected_at": "2026-01-01T00:00:00.000Z",
        "movie": {"ids": {"slug": slug}},
        "metadata": metadata,
    }


def args_for(*argv):
    return single.parse_args(list(argv))


def test_parse_args_defaults():
    args = args_for("--movie-slug", "nobody-2021")

    assert (args.media_type, args.resolution, args.audio_channels) == ("digital", "hd_720p", "2.0")
    assert args.audio is None and args.hdr is None and not args.is_3d


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--movie-slug", "x", "--check-only", "--commit"], "cannot be used together"),
        (["--movie-slug", "x", "--check-only", "--force"], "cannot be used together"),
        (["--movie-slug", "x", "--force"], "--force requires --commit"),
    ],
)
def test_validate_args_rejects_conflicting_flags(argv, message):
    with pytest.raises(single.UsageError, match=message):
        single.validate_args(args_for(*argv))


def test_resolve_identity_from_movie_url():
    identity = single.resolve_identity(
        args_for("--url", "https://app.trakt.tv/movies/nobody-2021?mode=x")
    )

    assert identity == single.ItemIdentity(is_movie=True, movie_slug="nobody-2021")


def test_resolve_identity_from_old_style_episode_url():
    identity = single.resolve_identity(
        args_for("--url", "https://trakt.tv/shows/severance/seasons/2/episodes/3")
    )

    assert identity == single.ItemIdentity(False, "severance", 2, 3)


def test_resolve_identity_from_query_episode_url():
    identity = single.resolve_identity(
        args_for("--url", "https://app.trakt.tv/shows/severance?season=1&episode=9")
    )

    assert (identity.show_slug, identity.season, identity.episode) == ("severance", 1, 9)


def test_resolve_identity_accepts_specials_season_zero():
    from_url = single.resolve_identity(
        args_for("--url", "https://trakt.tv/shows/doctor-who/seasons/0/episodes/4")
    )
    from_flags = single.resolve_identity(
        args_for("--show-slug", "doctor-who", "--season", "0", "--episode", "4")
    )

    assert from_url == from_flags == single.ItemIdentity(False, "doctor-who", 0, 4)


def test_explicit_season_overrides_url():
    identity = single.resolve_identity(
        args_for("--url", "https://trakt.tv/shows/x/seasons/2/episodes/3", "--season", "0")
    )

    assert (identity.season, identity.episode) == (0, 3)


def test_unparseable_url_falls_back_to_movie_slug(capsys):
    identity = single.resolve_identity(
        args_for("--url", "https://example.com/not-trakt", "--movie-slug", "nobody-2021")
    )

    assert identity == single.ItemIdentity(is_movie=True, movie_slug="nobody-2021")
    assert "Could not parse --url" in capsys.readouterr().out


def test_unparseable_url_falls_back_to_show_flags():
    identity = single.resolve_identity(
        args_for(
            "--url",
            "https://example.com/x",
            "--show-slug",
            "severance",
            "--season",
            "1",
            "--episode",
            "2",
        )
    )

    assert identity == single.ItemIdentity(False, "severance", 1, 2)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "You must supply"),
        (["--url", "https://example.com/x"], "Missing show slug"),
        (["--show-slug", "severance", "--season", "1"], "Missing show slug, season, or episode"),
    ],
)
def test_resolve_identity_rejects_incomplete_identity(argv, message):
    with pytest.raises(single.UsageError, match=message):
        single.resolve_identity(args_for(*argv))


def test_fetch_item_builds_movie_and_episode_paths(fake_trakt):
    fake = fake_trakt()

    single.fetch_item(single.ItemIdentity(True, movie_slug="nobody-2021"))
    single.fetch_item(single.ItemIdentity(False, "severance", 0, 2))

    assert fake.requests == [
        ("GET", "/movies/nobody-2021?extended=full"),
        ("GET", "/shows/severance/seasons/0/episodes/2?extended=full"),
    ]


def test_fetch_item_turns_404_into_usage_error(monkeypatch):
    def not_found(*args, **kwargs):
        raise requests.HTTPError(response=FakeResponse({}, status_code=404))

    monkeypatch.setattr(single.trakt, "_request_with_refresh", not_found)

    with pytest.raises(single.UsageError, match="Not found on Trakt"):
        single.fetch_item(single.ItemIdentity(True, movie_slug="missing"))


def test_fetch_item_reraises_other_http_errors(monkeypatch):
    def server_error(*args, **kwargs):
        raise requests.HTTPError(response=FakeResponse({}, status_code=500))

    monkeypatch.setattr(single.trakt, "_request_with_refresh", server_error)

    with pytest.raises(requests.HTTPError):
        single.fetch_item(single.ItemIdentity(True, movie_slug="x"))


def test_collection_lookup_uses_paginated_fetch_and_matches_episode(fake_trakt):
    shows = [
        {"show": {"ids": {"slug": "other"}}, "seasons": []},
        {
            "show": {"ids": {"slug": "severance"}},
            "seasons": [
                {"number": 1, "episodes": [{"number": 2, "metadata": {"resolution": "x"}}]},
                {"number": 2, "episodes": [{"number": 1}, {"number": 2, "metadata": {}}]},
            ],
        },
    ]
    fake = fake_trakt(movies=[movie_entry("other"), movie_entry("nobody-2021")], shows=shows)

    movie = single.fetch_collection_entry(single.ItemIdentity(True, movie_slug="nobody-2021"))
    episode = single.fetch_collection_entry(single.ItemIdentity(False, "severance", 2, 2))
    missing = single.fetch_collection_entry(single.ItemIdentity(False, "severance", 3, 1))

    assert movie["movie"]["ids"]["slug"] == "nobody-2021"
    assert episode == {"number": 2, "metadata": {}}
    assert missing is None
    assert ("GET", "/sync/collection/movies?extended=full") in fake.requests


def test_build_payload_for_movie_and_episode_with_optional_tags():
    args = args_for(
        "--movie-slug",
        "x",
        "--resolution",
        "uhd_4k",
        "--audio",
        "dolby_atmos",
        "--audio-channels",
        "7.1",
        "--hdr",
        "dolby_vision",
        "--is-3d",
    )
    movie = single.build_payload(args, single.ItemIdentity(True, movie_slug="x"))["movies"][0]
    episode_payload = single.build_payload(
        args_for("--show-slug", "s"), single.ItemIdentity(False, "s", 0, 1)
    )

    assert movie["ids"] == {"slug": "x"}
    assert {k: movie[k] for k in ("media_type", "resolution", "audio", "audio_channels")} == {
        "media_type": "digital",
        "resolution": "uhd_4k",
        "audio": "dolby_atmos",
        "audio_channels": "7.1",
    }
    assert movie["hdr"] == "dolby_vision" and movie["3d"] is True
    show = episode_payload["shows"][0]
    assert show["ids"] == {"slug": "s"}
    assert show["seasons"][0]["number"] == 0
    episode = show["seasons"][0]["episodes"][0]
    assert episode["number"] == 1
    assert "audio" not in episode and "hdr" not in episode and "3d" not in episode


def test_check_only_reports_metadata_without_writing(fake_trakt, capsys):
    fake = fake_trakt(movies=[movie_entry("nobody-2021", resolution="uhd_4k", hdr="hdr10")])

    assert single.run(args_for("--movie-slug", "nobody-2021", "--check-only")) is True

    out = capsys.readouterr().out
    assert "IS in your collection" in out and "uhd_4k" in out and "hdr10" in out
    assert fake.posted == []


def test_check_only_returns_false_when_not_collected(fake_trakt, capsys):
    fake_trakt()

    assert single.run(args_for("--movie-slug", "nobody-2021", "--check-only")) is False
    assert "is not in your collection" in capsys.readouterr().out


def test_preview_does_not_post(fake_trakt, capsys):
    fake = fake_trakt()

    assert single.run(args_for("--movie-slug", "nobody-2021")) is True

    assert fake.posted == []
    assert "Preview only" in capsys.readouterr().out


def test_commit_prompts_and_cancels_on_no(fake_trakt, monkeypatch, capsys):
    fake = fake_trakt()
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "n")

    assert single.run(args_for("--movie-slug", "nobody-2021", "--commit")) is True

    assert prompts and fake.posted == []
    assert "Cancelled" in capsys.readouterr().out


def test_commit_treats_closed_stdin_as_cancel(fake_trakt, monkeypatch):
    fake = fake_trakt()

    def closed(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)

    assert single.run(args_for("--movie-slug", "nobody-2021", "--commit")) is True
    assert fake.posted == []


def test_commit_after_yes_posts_and_verifies(fake_trakt, monkeypatch, capsys):
    added = movie_entry("nobody-2021", resolution="hd_720p")
    fake = fake_trakt(after_post=([added], []))
    monkeypatch.setattr("builtins.input", lambda prompt: " Y ")

    assert single.run(args_for("--movie-slug", "nobody-2021", "--commit")) is True

    assert fake.posted[0]["movies"][0]["ids"] == {"slug": "nobody-2021"}
    out = capsys.readouterr().out
    assert "Added to collection" in out and "IS in your collection" in out


def test_force_commit_skips_prompt_and_fails_when_verification_misses(fake_trakt, monkeypatch):
    fake = fake_trakt()

    def no_prompt(prompt):
        raise AssertionError("prompt should be skipped")

    monkeypatch.setattr("builtins.input", no_prompt)

    assert single.run(args_for("--movie-slug", "nobody-2021", "--commit", "--force")) is False
    assert len(fake.posted) == 1


def test_existing_entry_is_shown_before_retag(fake_trakt, capsys):
    fake_trakt(movies=[movie_entry("nobody-2021", resolution="sd_480p")])

    single.run(args_for("--movie-slug", "nobody-2021"))

    assert "already in your collection" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("result", "expected_ok", "message"),
    [
        ({"updated": {"episodes": 1}}, True, "Metadata updated"),
        ({"existing": {"episodes": 1}}, True, "identical metadata"),
        ({"not_found": {"episodes": [{"number": 1}]}}, False, "not_found"),
        ({"unexpected": True}, True, "Unexpected response shape"),
    ],
)
def test_commit_and_verify_reports_each_result(fake_trakt, capsys, result, expected_ok, message):
    shows = [
        {
            "show": {"ids": {"slug": "s"}},
            "seasons": [{"number": 1, "episodes": [{"number": 1, "metadata": {}}]}],
        }
    ]
    fake_trakt(shows=shows, post_result=result)
    identity = single.ItemIdentity(False, "s", 1, 1)

    assert single.commit_and_verify(identity, {"shows": []}, "Episode") is expected_ok
    assert message in capsys.readouterr().out


def run_main(monkeypatch, argv, run_result=None, error=None):
    monkeypatch.setattr("sys.argv", ["add_trakt_collection.py", *argv])

    def fake_run(args):
        if error is not None:
            raise error
        return run_result

    monkeypatch.setattr(single, "run", fake_run)
    with pytest.raises(SystemExit) as exit_info:
        single.main()
    return exit_info.value.code


@pytest.mark.parametrize(
    ("run_result", "error", "code"),
    [
        (True, None, 0),
        (False, None, 1),
        (None, single.UsageError("bad"), 2),
        (None, requests.HTTPError("500"), 1),
        (None, requests.ConnectionError("offline"), 1),
    ],
)
def test_main_exit_codes(monkeypatch, capsys, run_result, error, code):
    assert run_main(monkeypatch, ["--movie-slug", "x"], run_result, error) == code
    if error is not None:
        assert str(error) in capsys.readouterr().err

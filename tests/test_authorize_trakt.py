from scripts.authorize_trakt import save_tokens, write_env


def test_replace_tokens_preserves_other_env_values(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=old-access\n"
        "TRAKT_REFRESH_TOKEN=old-refresh\nSMTP_PASSWORD=keep-private\n",
        encoding="utf-8",
    )

    write_env(
        env_path,
        {"TRAKT_ACCESS_TOKEN": "new-access", "TRAKT_REFRESH_TOKEN": "new-refresh"},
    )

    assert env_path.read_text(encoding="utf-8") == (
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=new-access\n"
        "TRAKT_REFRESH_TOKEN=new-refresh\nSMTP_PASSWORD=keep-private\n"
    )


def test_save_tokens_uses_existing_db_token_file_only(tmp_path):
    env_path = tmp_path / ".env"
    token_path = tmp_path / "data" / "tokens.env"
    token_path.parent.mkdir()
    env_path.write_text(
        "TRAKT_CLIENT_ID=public-id\nTRAKT_ACCESS_TOKEN=old\nTRAKT_REFRESH_TOKEN=old\n",
        encoding="utf-8",
    )
    token_path.write_text("TRAKT_ACCESS_TOKEN=rotated\nTRAKT_REFRESH_TOKEN=rotated\n")

    saved_path = save_tokens(
        env_path,
        {"DB_PATH": str(token_path.parent / "prices.db")},
        "new-access",
        "new-refresh",
    )

    assert saved_path == token_path
    assert env_path.read_text(encoding="utf-8") == "TRAKT_CLIENT_ID=public-id\n"
    assert token_path.read_text(encoding="utf-8") == (
        "TRAKT_ACCESS_TOKEN=new-access\nTRAKT_REFRESH_TOKEN=new-refresh\n"
    )

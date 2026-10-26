"""Unit tests for the origin/identity half of the sessions work (migration 044).

Pure functions only, no DB and no network: `_resolve_session_source` maps three
request facts to a label, and `extract_caller_profile` digs an email and a display
name out of whatever shape the auth service returned. The DB-touching half
(`list_sessions`' filters/sort, `get_or_create_session`'s fill-if-empty backfill) is
verified by compiling the queries against the real models — see
docs/plan/chat-and-rag/api-tests.md — rather than by faking a whole
AsyncSession here for little extra signal.
"""

from utils.auth.auth_utils import extract_caller_profile


# ---------------------------------------------------------------------------
# _resolve_session_source — the embed flags win over the auth mode
# ---------------------------------------------------------------------------

def _source(key_type, from_widget=False, from_full_screen=False):
    # Imported lazily: routers/chat.py pulls in the whole chat graph at import
    # time, so keep that cost inside the tests that actually need it.
    from routers.chat import _resolve_session_source
    return _resolve_session_source(
        key_type, from_widget=from_widget, from_full_screen=from_full_screen
    )


def test_public_caller_is_a_widget():
    # A credential-less caller can only have come through an embed.
    assert _source("public") == "widget"


def test_master_key_is_api():
    assert _source("master_key") == "api"


def test_signed_in_caller_defaults_to_dashboard():
    assert _source("session_or_api_key") == "dashboard"


def test_from_widget_beats_a_signed_in_session():
    # The whole point of the new flag: this case is invisible server-side.
    assert _source("session_or_api_key", from_widget=True) == "widget"


def test_full_screen_embed_is_also_a_widget():
    assert _source("session_or_api_key", from_full_screen=True) == "widget"


# ---------------------------------------------------------------------------
# extract_caller_profile
# ---------------------------------------------------------------------------

def test_no_session_data_yields_nothing():
    assert extract_caller_profile(None) == (None, None)


def test_email_is_lowercased_and_stripped():
    email, _ = extract_caller_profile({"email": "  Priya.Nair@FutureSmart.ai "})
    assert email == "priya.nair@futuresmart.ai"


def test_top_level_name_wins_over_claims():
    _, name = extract_caller_profile(
        {"email": "a@b.com", "name": "Priya Nair", "all_claims": {"name": "stale"}}
    )
    assert name == "Priya Nair"


def test_name_falls_back_to_claims():
    _, name = extract_caller_profile({"email": "a@b.com", "all_claims": {"name": "Arjun Desai"}})
    assert name == "Arjun Desai"


def test_given_and_family_name_are_joined():
    _, name = extract_caller_profile(
        {"email": "a@b.com", "all_claims": {"given_name": "Arjun", "family_name": "Desai"}}
    )
    assert name == "Arjun Desai"


def test_blank_name_is_none_not_empty_string():
    # A caller that renders `user_name or user_email` must not get an empty label.
    _, name = extract_caller_profile({"email": "a@b.com", "all_claims": {"name": "   "}})
    assert name is None


def test_missing_email_is_none():
    email, name = extract_caller_profile({"all_claims": {"name": "Nobody"}})
    assert email is None and name == "Nobody"

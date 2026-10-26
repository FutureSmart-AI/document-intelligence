"""Keep the hosted cookie contract while isolating standalone operator sessions."""
import os


def session_cookie_name():
    return "di_session" if os.getenv("AUTH_MODE") == "standalone" else "session"

"""A stand-in for the dashboard's saved keys (dashboard/app/keys.py): what each person saved, looked up by THE EMAIL IN THE COOKIE the app forwards, exactly
as the real dashboard does. `save_key(client, key)` plays the person saving their key on their Brassworks profile."""
import jwt
from shared_python import settings, user_keys

STORE = {}  # email -> the Anthropic key they saved


def _email(token: str) -> str:
    return jwt.decode(token, options={"verify_signature": False})["email"].strip().lower()


def _cookie_email(client) -> str:
    return _email(client.cookies.get("oidc_session"))


def save_key(client, key: str) -> None:
    STORE[_cookie_email(client)] = key


def remove_key(client) -> bool:
    return STORE.pop(_cookie_email(client), None) is not None


def install(monkeypatch) -> None:
    STORE.clear()
    monkeypatch.setattr(settings, "DASHBOARD_API_BASE", "http://127.0.0.1:8090")

    def fake_get(self, path):
        if not self.cookie:
            return None  # signed in as nobody
        email = _email(self.cookie.split("=", 1)[1])
        if path == "/internal/keys":
            return {"keys": {"anthropic": {"last4": STORE[email][-4:], "added_at": "2026-11-23T10:00:00Z"}} if email in STORE else {}}
        provider = path.rsplit("/", 1)[1]
        key = STORE.get(email) if provider == "anthropic" else None
        return {"key": key} if key else None

    monkeypatch.setattr(user_keys.KeySession, "_get", fake_get)

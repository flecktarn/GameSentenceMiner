"""Create cards in the Japanese SRS app (https://github.com/flecktarn/japanese-srs).

This is an alternative to the Anki flow: instead of waiting for Yomitan to add
a note to Anki, GSM looks the mined word up through the app's Jisho endpoint
and creates the card directly over the app's REST API, with the game line as
its example sentence.
"""

from __future__ import annotations

from typing import Any

import requests

from GameSentenceMiner.util.config.configuration import JapaneseSrs, get_config, get_master_config, logger

REQUEST_TIMEOUT_SECONDS = 20


class JapaneseSrsError(Exception):
    """A failure worth showing to the user as-is."""


class JapaneseSrsAuthError(JapaneseSrsError):
    pass


def get_settings() -> JapaneseSrs:
    get_config()  # Ensures the master config is loaded.
    return get_master_config().japanese_srs


def _error_detail(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(data, dict) and data.get("detail"):
        return str(data["detail"])
    return f"HTTP {response.status_code}: {data}"


def login(api_url: str, username: str, password: str) -> str:
    """Exchange a username and password for an API token."""
    api_url = api_url.strip().rstrip("/")
    try:
        response = requests.post(
            f"{api_url}/auth/login/",
            json={"username": username, "password": password},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise JapaneseSrsError(f"Could not reach {api_url}: {exc}") from exc
    if response.status_code != 200:
        raise JapaneseSrsError(f"Login failed: {_error_detail(response)}")
    token = response.json().get("token")
    if not token:
        raise JapaneseSrsError("Login succeeded but no token was returned.")
    return token


class JapaneseSrsClient:
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.strip().rstrip("/")
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Token {token}"

    @classmethod
    def from_settings(cls, settings: JapaneseSrs | None = None) -> JapaneseSrsClient:
        settings = settings or get_settings()
        if not settings.enabled:
            raise JapaneseSrsError("Japanese SRS is turned off in GSM settings.")
        if not settings.token:
            raise JapaneseSrsError("Log in to Japanese SRS in GSM settings first.")
        return cls(settings.api_url, settings.token)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        try:
            response = self.session.request(
                method, f"{self.api_url}/{path.lstrip('/')}", timeout=REQUEST_TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as exc:
            raise JapaneseSrsError(f"Could not reach {self.api_url}: {exc}") from exc
        if response.status_code == 401:
            raise JapaneseSrsAuthError("Japanese SRS rejected the saved login. Log in again in GSM settings.")
        if not response.ok:
            raise JapaneseSrsError(f"{method} {path} failed: {_error_detail(response)}")
        return response.json()

    def lookup(self, word: str) -> list[dict[str, Any]]:
        return self._request("GET", "jisho/", params={"keyword": word}).get("results", [])

    def _list_decks(self) -> list[dict[str, Any]]:
        decks: list[dict[str, Any]] = []
        data = self._request("GET", "decks/")
        while True:
            if isinstance(data, list):  # Unpaginated response.
                return decks + data
            decks.extend(data.get("results", []))
            next_url = data.get("next")
            if not next_url:
                return decks
            try:
                response = self.session.get(next_url, timeout=REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
            except requests.RequestException as exc:
                raise JapaneseSrsError(f"Could not list decks: {exc}") from exc
            data = response.json()

    def get_or_create_deck(self, name: str) -> dict[str, Any]:
        for deck in self._list_decks():
            if str(deck.get("name", "")).strip().casefold() == name.casefold():
                return deck
        logger.info(f"Creating Japanese SRS deck '{name}'.")
        return self._request("POST", "decks/", json={"name": name})

    def create_card(self, deck_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "cards/", json={**fields, "deck": deck_id})


def pick_entry(word: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer an exact spelling or reading match; Jisho ranks by relevance otherwise."""
    for entry in results:
        if entry.get("kanji") == word:
            return entry
    for entry in results:
        if word in (entry.get("readings") or [entry.get("reading")]):
            return entry
    return results[0] if results else None


def build_card_fields(word: str, sentence: str, translation: str, entry: dict[str, Any] | None) -> dict[str, Any]:
    fields: dict[str, Any] = {"kanji": word, "reading": "", "meaning": "", "parts_of_speech": "", "jlpt": ""}
    if entry:
        for key in fields:
            if entry.get(key):
                fields[key] = entry[key]
    sentence = sentence.strip()
    if sentence:
        fields["example_sentences"] = [{"ja": sentence, "reading": "", "en": translation.strip()}]
    return fields


def add_word(word: str, sentence: str = "", translation: str = "") -> dict[str, Any]:
    """Look up ``word`` and add it to the configured deck. Returns the created card."""
    word = word.strip()
    if not word:
        raise JapaneseSrsError("Select a word in the line first.")
    settings = get_settings()
    client = JapaneseSrsClient.from_settings(settings)

    try:
        entry = pick_entry(word, client.lookup(word))
    except JapaneseSrsAuthError:
        raise
    except JapaneseSrsError as exc:
        # A dictionary outage shouldn't lose the word; the card can be filled in later.
        logger.warning(f"Japanese SRS dictionary lookup failed for '{word}': {exc}")
        entry = None

    deck = client.get_or_create_deck(settings.deck_name)
    card = client.create_card(deck["id"], build_card_fields(word, sentence, translation, entry))
    logger.info(f"Added '{card.get('kanji')}' to Japanese SRS deck '{deck.get('name')}'.")
    return card

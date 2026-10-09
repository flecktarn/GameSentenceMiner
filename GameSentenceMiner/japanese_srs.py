"""Create cards in the Japanese SRS app (https://github.com/flecktarn/japanese-srs).

This is an alternative to the Anki flow: instead of waiting for Yomitan to add
a note to Anki, GSM looks the mined word up through the app's Jisho endpoint
and creates the card directly over the app's REST API, with the game line as
its first example sentence and a screenshot of that moment attached.
"""

from __future__ import annotations

import hashlib
import io
import re
import threading
import time
from collections import OrderedDict
from typing import Any

import requests

from GameSentenceMiner.util.config.configuration import JapaneseSrs, get_config, get_master_config, logger

REQUEST_TIMEOUT_SECONDS = 20
# Screenshots are taken as each line arrives, so a word mined from an older line
# still gets that line's moment. Kept in memory for the most recent lines only.
SCREENSHOT_CACHE_LINES = 100
SCREENSHOT_MAX_SIDE = 1280
SCREENSHOT_JPEG_QUALITY = 82
# Page/advance markers some games draw at the end of a line.
TRAILING_LINE_MARKERS = "▶▷►▸▼▽◆◇■□⏎↵"
# How long the list of words already in the SRS is trusted before a background refresh.
KNOWN_WORDS_TTL_SECONDS = 300
# Same palette as the SRS app's tag colours; a game's tag gets a stable colour from its name.
TAG_COLORS = ("#c8433d", "#d99a3c", "#c9b03a", "#4e9d6c", "#3f9aa0", "#3f7fb0", "#7a5bb5", "#b5577f", "#8c8171")


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

    def all_card_words(self) -> set[str]:
        words: set[str] = set()
        data = self._request("GET", "cards/", params={"page_size": 2000})
        while True:
            for card in data.get("results", []):
                words.update(w for w in (card.get("kanji"), card.get("reading")) if w)
            next_url = data.get("next")
            if not next_url:
                return words
            try:
                response = self.session.get(next_url, timeout=REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
            except requests.RequestException as exc:
                raise JapaneseSrsError(f"Could not list cards: {exc}") from exc
            data = response.json()

    def get_or_create_tag(self, name: str) -> dict[str, Any]:
        for tag in self._request("GET", "tags/"):
            if str(tag.get("name", "")).casefold() == name.casefold():
                return tag
        color = TAG_COLORS[int(hashlib.md5(name.encode()).hexdigest(), 16) % len(TAG_COLORS)]
        logger.info(f"Creating Japanese SRS tag '{name}'.")
        return self._request("POST", "tags/", json={"name": name, "color": color})

    def attach_image(self, card_id: int, jpeg: bytes) -> dict[str, Any]:
        return self._request("POST", f"cards/{card_id}/image/", files={"image": ("screenshot.jpg", jpeg, "image/jpeg")})


_screenshots: OrderedDict[str, bytes] = OrderedDict()
_screenshots_lock = threading.Lock()


def capture_screenshot_jpeg() -> bytes | None:
    """The current OBS game frame as a compressed JPEG, or None if OBS has no frame."""
    from GameSentenceMiner import obs

    img = obs.get_screenshot_PIL(compression=90, img_format="jpg", log_missing_source=False, suppress_errors=True)
    if img is None:
        return None
    img = img.convert("RGB")
    img.thumbnail((SCREENSHOT_MAX_SIDE, SCREENSHOT_MAX_SIDE))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def _remember_screenshot(line_id: str) -> None:
    try:
        jpeg = capture_screenshot_jpeg()
    except Exception as exc:  # noqa: BLE001 - a missed screenshot must never disturb text intake.
        logger.debug(f"Japanese SRS: couldn't screenshot line {line_id}: {exc}")
        return
    if jpeg is None:
        return
    with _screenshots_lock:
        _screenshots[line_id] = jpeg
        while len(_screenshots) > SCREENSHOT_CACHE_LINES:
            _screenshots.popitem(last=False)


def on_new_line(line: Any) -> None:
    """Called for each new game line: screenshot the moment in the background."""
    line_id = getattr(line, "id", None)
    if line_id is None or not get_settings().enabled:
        return
    threading.Thread(target=_remember_screenshot, args=(str(line_id),), daemon=True, name="srs-screenshot").start()


def screenshot_for_line(line_id: str | None, is_latest_line: bool) -> bytes | None:
    with _screenshots_lock:
        jpeg = _screenshots.get(str(line_id)) if line_id is not None else None
    if jpeg is None and is_latest_line:
        # The line arrived before Japanese SRS was on, but it's still on screen.
        jpeg = capture_screenshot_jpeg()
    return jpeg


def clean_sentence(sentence: str) -> str:
    return sentence.strip().rstrip(TRAILING_LINE_MARKERS).strip()


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
    sentence = clean_sentence(sentence)
    if sentence:
        # "source" lets the app keep this sentence first when it generates more.
        fields["example_sentences"] = [{"ja": sentence, "reading": "", "en": translation.strip(), "source": "game"}]
    return fields


_lookup_cache: OrderedDict[str, dict[str, Any] | None] = OrderedDict()
_lookup_cache_lock = threading.Lock()
LOOKUP_CACHE_SIZE = 500


def lookup_entry(word: str, client: JapaneseSrsClient | None = None) -> dict[str, Any] | None:
    """Best dictionary entry for ``word`` (via the SRS app's Jisho endpoint), cached."""
    with _lookup_cache_lock:
        if word in _lookup_cache:
            _lookup_cache.move_to_end(word)
            return _lookup_cache[word]
    entry = pick_entry(word, (client or JapaneseSrsClient.from_settings()).lookup(word))
    with _lookup_cache_lock:
        _lookup_cache[word] = entry
        while len(_lookup_cache) > LOOKUP_CACHE_SIZE:
            _lookup_cache.popitem(last=False)
    return entry


def add_word(
    word: str, sentence: str = "", translation: str = "", screenshot: bytes | None = None, game: str = ""
) -> tuple[dict[str, Any], str]:
    """Look up ``word`` and add it to the configured deck, tagged with ``game``.

    Returns the created card and a warning ("" if none). A failed screenshot
    upload or game tag is only a warning: the card itself was saved.
    """
    word = word.strip()
    if not word:
        raise JapaneseSrsError("Select a word in the line first.")
    settings = get_settings()
    client = JapaneseSrsClient.from_settings(settings)

    try:
        entry = lookup_entry(word, client)
    except JapaneseSrsAuthError:
        raise
    except JapaneseSrsError as exc:
        # A dictionary outage shouldn't lose the word; the card can be filled in later.
        logger.warning(f"Japanese SRS dictionary lookup failed for '{word}': {exc}")
        entry = None

    warnings = []
    fields = build_card_fields(word, sentence, translation, entry)
    game = game.strip()
    if settings.tag_with_game and game:
        try:
            fields["tags"] = [client.get_or_create_tag(game[:40])["id"]]
        except JapaneseSrsAuthError:
            raise
        except JapaneseSrsError as exc:
            logger.warning(f"Japanese SRS: couldn't tag the card with '{game}': {exc}")
            warnings.append(f"The game tag couldn't be added: {exc}")

    deck = client.get_or_create_deck(settings.deck_name)
    card = client.create_card(deck["id"], fields)
    logger.info(f"Added '{card.get('kanji')}' to Japanese SRS deck '{deck.get('name')}'.")
    remember_known_word(word, card.get("kanji", ""), card.get("reading", ""))

    if screenshot:
        try:
            card = client.attach_image(card["id"], screenshot)
        except JapaneseSrsError as exc:
            logger.warning(f"Japanese SRS: card added but the screenshot upload failed: {exc}")
            warnings.append(f"The screenshot couldn't be attached: {exc}")
    return card, " ".join(warnings)


# --- Words already in the SRS (for marking them in the Text Feed) ---

_known_words: set[str] = set()
_known_words_fetched_at = 0.0
_known_words_refreshing = False
_known_words_lock = threading.Lock()


def _refresh_known_words() -> None:
    global _known_words, _known_words_fetched_at, _known_words_refreshing
    try:
        words = JapaneseSrsClient.from_settings().all_card_words()
        with _known_words_lock:
            _known_words = words
            _known_words_fetched_at = time.monotonic()
    except Exception as exc:  # noqa: BLE001 - marking known words is best-effort.
        logger.debug(f"Japanese SRS: couldn't refresh known words: {exc}")
    finally:
        with _known_words_lock:
            _known_words_refreshing = False


def known_words() -> set[str]:
    """Words on cards already in the SRS. Never blocks: a stale list refreshes in the background."""
    global _known_words_refreshing
    with _known_words_lock:
        stale = time.monotonic() - _known_words_fetched_at > KNOWN_WORDS_TTL_SECONDS
        start = stale and not _known_words_refreshing and get_settings().is_configured()
        if start:
            _known_words_refreshing = True
        words = set(_known_words)
    if start:
        threading.Thread(target=_refresh_known_words, daemon=True, name="srs-known-words").start()
    return words


def remember_known_word(*words: str) -> None:
    with _known_words_lock:
        _known_words.update(w for w in words if w)


# --- Splitting a line into clickable vocabulary ---

VOCAB_PARTS_OF_SPEECH = {
    "noun",
    "verb",
    "i_adjective",
    "adverb",
    "adnominal_adjective",
    "interjection",
    "conjunction",
}
# Kana verbs MeCab tags as verbs but that act as grammar here (passive, progressive, ...).
GRAMMAR_VERBS = {"れる", "られる", "せる", "させる", "いる", "ある", "おる", "しまう", "ちゃう", "おく", "てる", "とく"}
_KANA_ONLY = re.compile(r"^[\u3040-\u30ffー]+$")
_HAS_JAPANESE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff々]")


def _is_vocab(surface: str, base: str, pos: str) -> bool:
    if pos not in VOCAB_PARTS_OF_SPEECH or not _HAS_JAPANESE.search(surface):
        return False
    if pos == "verb" and base in GRAMMAR_VERBS:
        return False
    # Single-kana nouns are almost always grammar (の, ん, こ).
    return not (pos == "noun" and len(surface) == 1 and _KANA_ONLY.match(surface))


def split_line(text: str, known: set[str] | None = None) -> list[dict[str, Any]]:
    """Segments covering ``text`` exactly; vocabulary segments carry their dictionary form."""
    from GameSentenceMiner.tokenizer import tokenizer

    known = known or set()
    segments: list[dict[str, Any]] = []
    cursor = 0
    for token in tokenizer.translate(text):
        surface = getattr(token, "word", "") or ""
        start = text.find(surface, cursor) if surface else -1
        if start < 0:
            continue  # The tokenizer normalised something; leave it inside the next gap.
        if start > cursor:
            segments.append({"text": text[cursor:start]})
        part_of_speech = getattr(getattr(token, "part_of_speech", None), "name", "") or ""
        base = getattr(token, "headword", None) or surface
        segment: dict[str, Any] = {"text": surface}
        if _is_vocab(surface, base, part_of_speech):
            segment.update(base=base, pos=part_of_speech, in_srs=base in known or surface in known)
        segments.append(segment)
        cursor = start + len(surface)
    if cursor < len(text):
        segments.append({"text": text[cursor:]})
    return segments

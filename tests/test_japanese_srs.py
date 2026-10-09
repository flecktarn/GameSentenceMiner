from types import SimpleNamespace

import pytest

from GameSentenceMiner import japanese_srs
from GameSentenceMiner.util.config.configuration import Config, JapaneseSrs

API = "https://srs.example/api"


class FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._data


class FakeBackend:
    """Mimics the japanese-srs REST API for the endpoints GSM calls."""

    def __init__(self, decks=None, jisho=None, jisho_status=200):
        self.decks = list(decks or [])
        self.jisho = jisho or []
        self.jisho_status = jisho_status
        self.cards = []
        self.calls = []
        self.images = {}
        self.image_status = 200

    def request(self, method, url, timeout=None, params=None, json=None, files=None):
        path = url.removeprefix(API)
        self.calls.append((method, path, params, json))
        if path.endswith("/image/") and method == "POST":
            if self.image_status != 200:
                return FakeResponse(self.image_status, {"detail": "Disk full"})
            card_id = int(path.split("/")[2])
            card = next(c for c in self.cards if c["id"] == card_id)
            card["image_url"] = f"{API}/media/cards/abc.jpg"
            self.images[card_id] = files["image"][1]
            return FakeResponse(200, card)
        if path == "/jisho/":
            return FakeResponse(self.jisho_status, {"results": self.jisho, "detail": "Jisho down"})
        if path == "/decks/" and method == "GET":
            return FakeResponse(200, {"count": len(self.decks), "next": None, "results": self.decks})
        if path == "/decks/" and method == "POST":
            deck = {"id": len(self.decks) + 1, **json}
            self.decks.append(deck)
            return FakeResponse(201, deck)
        if path == "/cards/" and method == "POST":
            card = {"id": len(self.cards) + 1, **json}
            self.cards.append(card)
            return FakeResponse(201, card)
        return FakeResponse(404, {"detail": "Not found."})


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend(
        jisho=[
            {"kanji": "食べる", "reading": "たべる", "readings": ["たべる"], "meaning": "to eat", "jlpt": "jlpt-n5"},
        ]
    )
    monkeypatch.setattr(japanese_srs.requests.Session, "request", lambda self, *a, **kw: fake.request(*a, **kw))
    settings = JapaneseSrs(enabled=True, api_url=API, token="abc", deck_name="Default")
    monkeypatch.setattr(japanese_srs, "get_settings", lambda: settings)
    fake.settings = settings
    return fake


def test_add_word_creates_default_deck_and_card(backend):
    card, warning = japanese_srs.add_word("食べた", "パンを食べたい。▶", "I want to eat bread.")

    assert backend.decks == [{"id": 1, "name": "Default"}]
    assert card["deck"] == 1
    assert card["kanji"] == "食べる"
    assert card["reading"] == "たべる"
    assert card["meaning"] == "to eat"
    assert card["example_sentences"] == [
        {"ja": "パンを食べたい。", "reading": "", "en": "I want to eat bread.", "source": "game"}
    ]
    assert warning == ""
    assert backend.images == {}


def test_add_word_reuses_existing_deck_case_insensitively(backend):
    backend.decks.append({"id": 7, "name": "default"})

    card, _ = japanese_srs.add_word("食べる")

    assert card["deck"] == 7
    assert len(backend.decks) == 1
    assert "example_sentences" not in card


def test_add_word_still_creates_card_when_dictionary_fails(backend):
    backend.jisho_status = 502

    card, _ = japanese_srs.add_word("珍語", "珍語だ。")

    assert card["kanji"] == "珍語"
    assert card["meaning"] == ""


def test_add_word_rejects_bad_token_without_creating_anything(backend):
    backend.jisho_status = 401

    with pytest.raises(japanese_srs.JapaneseSrsAuthError, match="Log in again"):
        japanese_srs.add_word("食べる")
    assert backend.cards == []


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        (JapaneseSrs(enabled=False, token="abc"), "turned off"),
        (JapaneseSrs(enabled=True, token=""), "Log in"),
    ],
)
def test_add_word_requires_enabled_and_logged_in(monkeypatch, settings, message):
    monkeypatch.setattr(japanese_srs, "get_settings", lambda: settings)

    with pytest.raises(japanese_srs.JapaneseSrsError, match=message):
        japanese_srs.add_word("食べる")


def test_add_word_requires_a_word(backend):
    with pytest.raises(japanese_srs.JapaneseSrsError, match="Select a word"):
        japanese_srs.add_word("  ")


def test_pick_entry_prefers_exact_spelling_then_reading():
    results = [
        {"kanji": "猫舌", "reading": "ねこじた", "readings": ["ねこじた"]},
        {"kanji": "猫", "reading": "ねこ", "readings": ["ねこ"]},
    ]

    assert japanese_srs.pick_entry("猫", results)["kanji"] == "猫"
    assert japanese_srs.pick_entry("ねこ", results)["kanji"] == "猫"
    assert japanese_srs.pick_entry("ねこだ", results)["kanji"] == "猫舌"
    assert japanese_srs.pick_entry("ねこ", []) is None


def test_login_returns_token(monkeypatch):
    def fake_post(url, json, timeout):
        assert url == f"{API}/auth/login/"
        ok = json == {"username": "jacob", "password": "pw"}
        return FakeResponse(200, {"token": "t0k"}) if ok else FakeResponse(401, {"detail": "Invalid credentials."})

    monkeypatch.setattr(japanese_srs.requests, "post", fake_post)

    assert japanese_srs.login(f"{API}/", "jacob", "pw") == "t0k"
    with pytest.raises(japanese_srs.JapaneseSrsError, match="Invalid credentials"):
        japanese_srs.login(API, "jacob", "wrong")


def test_config_defaults_and_round_trip():
    config = Config.new()
    assert config.japanese_srs.enabled is False
    assert config.japanese_srs.deck_name == "Default"

    config.japanese_srs = JapaneseSrs(enabled=True, api_url=f"{API}/", token=" abc ", deck_name="")
    restored = Config.from_dict(config.to_dict())

    assert restored.japanese_srs.api_url == API
    assert restored.japanese_srs.token == "abc"
    assert restored.japanese_srs.deck_name == "Default"
    assert restored.japanese_srs.is_configured()


def test_older_config_without_section_loads_defaults():
    data = Config.new().to_dict()
    del data["japanese_srs"]

    assert Config.from_dict(data).japanese_srs == JapaneseSrs()


def test_add_route_uses_line_text_and_translation(monkeypatch):
    from GameSentenceMiner.web import texthooking_page

    line = SimpleNamespace(id="L1", text="パンを食べたい。", TL="I want to eat bread.")
    monkeypatch.setattr(texthooking_page, "get_event_line_by_id", lambda event_id: line if event_id == "L1" else None)
    monkeypatch.setattr(texthooking_page, "get_all_lines", lambda: [SimpleNamespace(id="L0"), SimpleNamespace(id="L1")])
    monkeypatch.setattr(
        japanese_srs, "screenshot_for_line", lambda line_id, latest: f"jpeg:{line_id}:{latest}".encode()
    )
    calls = []
    monkeypatch.setattr(
        japanese_srs, "add_word", lambda *args: calls.append(args) or ({"id": 1, "kanji": "食べる"}, "upload warning")
    )
    client = texthooking_page.app.test_client()

    response = client.post("/api/japanese-srs/add", json={"id": "L1", "word": "食べた"})

    assert response.status_code == 200
    assert response.get_json() == {"card": {"id": 1, "kanji": "食べる"}, "warning": "upload warning"}
    assert calls == [("食べた", "パンを食べたい。", "I want to eat bread.", b"jpeg:L1:True")]
    assert client.post("/api/japanese-srs/add", json={"id": "L1", "word": ""}).status_code == 400


def test_add_route_reports_srs_errors(monkeypatch):
    from GameSentenceMiner.web import texthooking_page

    monkeypatch.setattr(texthooking_page, "get_event_line_by_id", lambda event_id: None)
    monkeypatch.setattr(japanese_srs, "screenshot_for_line", lambda *_args: None)

    def fail(*_args):
        raise japanese_srs.JapaneseSrsError("Log in to Japanese SRS in GSM settings first.")

    monkeypatch.setattr(japanese_srs, "add_word", fail)

    response = texthooking_page.app.test_client().post("/api/japanese-srs/add", json={"id": "x", "word": "猫"})

    assert response.status_code == 502
    assert "Log in" in response.get_json()["error"]


def test_screenshot_is_uploaded_to_the_new_card(backend):
    card, warning = japanese_srs.add_word("食べる", "パンを食べたい。", screenshot=b"\xff\xd8jpeg")

    assert warning == ""
    assert card["image_url"].endswith("abc.jpg")
    assert backend.images == {card["id"]: b"\xff\xd8jpeg"}


def test_failed_screenshot_upload_keeps_the_card(backend):
    backend.image_status = 500

    _, warning = japanese_srs.add_word("食べる", screenshot=b"\xff\xd8jpeg")

    assert len(backend.cards) == 1
    assert "screenshot couldn't be attached" in warning


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("どうせ引き裂かれるなら、▶", "どうせ引き裂かれるなら、"), ("  台詞。 ▼ ", "台詞。"), ("▶で始まる", "▶で始まる")],
)
def test_clean_sentence_strips_trailing_page_markers(raw, clean):
    assert japanese_srs.clean_sentence(raw) == clean


@pytest.fixture
def fresh_screenshots(monkeypatch):
    monkeypatch.setattr(japanese_srs, "_screenshots", japanese_srs.OrderedDict())
    return japanese_srs._screenshots


def test_screenshots_are_cached_per_line_and_capped(monkeypatch, fresh_screenshots):
    shots = iter(range(1000))
    monkeypatch.setattr(japanese_srs, "capture_screenshot_jpeg", lambda: f"shot{next(shots)}".encode())
    for i in range(japanese_srs.SCREENSHOT_CACHE_LINES + 5):
        japanese_srs._remember_screenshot(f"line{i}")

    assert len(fresh_screenshots) == japanese_srs.SCREENSHOT_CACHE_LINES
    assert "line0" not in fresh_screenshots
    assert japanese_srs.screenshot_for_line("line7", is_latest_line=False) == b"shot7"


def test_uncached_line_only_gets_a_live_screenshot_if_still_on_screen(monkeypatch, fresh_screenshots):
    monkeypatch.setattr(japanese_srs, "capture_screenshot_jpeg", lambda: b"live")

    assert japanese_srs.screenshot_for_line("old", is_latest_line=False) is None
    assert japanese_srs.screenshot_for_line("newest", is_latest_line=True) == b"live"


def test_new_lines_are_only_captured_while_enabled(monkeypatch, fresh_screenshots):
    started = []
    monkeypatch.setattr(
        japanese_srs.threading, "Thread", lambda target, args, **kw: SimpleNamespace(start=lambda: started.append(args))
    )
    monkeypatch.setattr(japanese_srs, "get_settings", lambda: JapaneseSrs(enabled=False))
    japanese_srs.on_new_line(SimpleNamespace(id=5))
    monkeypatch.setattr(japanese_srs, "get_settings", lambda: JapaneseSrs(enabled=True))
    japanese_srs.on_new_line(SimpleNamespace(id=6))

    assert started == [("6",)]


def test_capture_compresses_to_a_bounded_jpeg(monkeypatch):
    from PIL import Image

    from GameSentenceMiner import obs

    monkeypatch.setattr(obs, "get_screenshot_PIL", lambda **kw: Image.new("RGBA", (2560, 1440), (10, 20, 30, 255)))
    jpeg = japanese_srs.capture_screenshot_jpeg()

    img = Image.open(japanese_srs.io.BytesIO(jpeg))
    assert img.format == "JPEG"
    assert max(img.size) == japanese_srs.SCREENSHOT_MAX_SIDE

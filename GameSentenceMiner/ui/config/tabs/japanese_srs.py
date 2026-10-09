from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from GameSentenceMiner.ui.config.safety import safe_config_callback

if TYPE_CHECKING:
    from GameSentenceMiner.ui.config.binding import BindingManager
    from GameSentenceMiner.ui.config_gui_qt import ConfigWindow

TOKEN_PATH = ("master", "japanese_srs", "token")


class _LoginResult(QObject):
    # (token, error); emitted from the login thread, delivered on the Qt thread.
    finished = pyqtSignal(str, str)


def build_japanese_srs_tab(window: ConfigWindow, binder: BindingManager, i18n: dict) -> QWidget:
    widget = QWidget()
    layout = QFormLayout(widget)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    intro = QLabel(
        "Send words from the Text Feed straight to the Japanese SRS app. Select a word in a line, "
        "then use the line's “Add to Japanese SRS” action (or press Alt+J). Yomitan and Anki are not needed."
    )
    intro.setWordWrap(True)
    layout.addRow(intro)

    enabled_check = QCheckBox()
    layout.addRow("Enabled:", enabled_check)
    binder.bind(("master", "japanese_srs", "enabled"), enabled_check)

    api_url_edit = QLineEdit()
    api_url_edit.setToolTip("Base URL of the Japanese SRS API, ending in /api")
    layout.addRow("API URL:", api_url_edit)
    binder.bind(("master", "japanese_srs", "api_url"), api_url_edit)

    tag_game_check = QCheckBox()
    tag_game_check.setToolTip("Adds a tag named after the game (the OBS scene), e.g. Higurashi, to each card.")
    layout.addRow("Tag cards with game:", tag_game_check)
    binder.bind(("master", "japanese_srs", "tag_with_game"), tag_game_check)

    deck_edit = QLineEdit()
    deck_edit.setToolTip("Cards are added to this deck. It is created if it doesn't exist.")
    layout.addRow("Deck:", deck_edit)
    binder.bind(("master", "japanese_srs", "deck_name"), deck_edit)

    username_edit = QLineEdit()
    layout.addRow("Username:", username_edit)
    binder.bind(("master", "japanese_srs", "username"), username_edit)

    password_edit = QLineEdit()
    password_edit.setEchoMode(QLineEdit.EchoMode.Password)
    password_edit.setToolTip("Only used to log in. GSM saves the API token, never the password.")
    # Not a config field, so keep it out of autosave.
    password_edit.setProperty("_gsm_autosave_connected", True)
    layout.addRow("Password:", password_edit)

    status_label = QLabel()
    status_label.setWordWrap(True)
    status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def refresh_status(token=None) -> None:
        token = window.editor.get_value(TOKEN_PATH) if token is None else token
        status_label.setText("Logged in." if token else "Not logged in.")

    login_button = QPushButton("Log in")
    logout_button = QPushButton("Log out")

    login_result = _LoginResult(widget)

    @safe_config_callback
    def on_login_finished(token: str, error: str) -> None:
        login_button.setEnabled(True)
        if error:
            status_label.setText(error)
            return
        password_edit.clear()
        window.editor.set_value(TOKEN_PATH, token)
        refresh_status(token)
        window.request_auto_save(immediate=True)

    login_result.finished.connect(on_login_finished)

    @safe_config_callback
    def on_login(*_args) -> None:
        from GameSentenceMiner import japanese_srs

        api_url = window.editor.get_value(("master", "japanese_srs", "api_url"))
        username, password = username_edit.text().strip(), password_edit.text()
        if not username or not password:
            status_label.setText("Enter your username and password.")
            return

        def worker() -> None:
            try:
                login_result.finished.emit(japanese_srs.login(api_url, username, password), "")
            except japanese_srs.JapaneseSrsError as exc:
                login_result.finished.emit("", str(exc))
            except Exception as exc:  # noqa: BLE001 - any failure must reach the dialog, not kill the thread.
                login_result.finished.emit("", f"Login failed: {exc}")

        login_button.setEnabled(False)
        status_label.setText("Logging in…")
        threading.Thread(target=worker, daemon=True, name="japanese-srs-login").start()

    @safe_config_callback
    def on_logout(*_args) -> None:
        window.editor.set_value(TOKEN_PATH, "")
        refresh_status("")
        window.request_auto_save(immediate=True)

    login_button.clicked.connect(on_login)
    password_edit.returnPressed.connect(on_login)
    logout_button.clicked.connect(on_logout)
    window.editor.subscribe(TOKEN_PATH, refresh_status)

    actions = QWidget()
    actions_layout = QHBoxLayout(actions)
    actions_layout.setContentsMargins(0, 0, 0, 0)
    actions_layout.addWidget(login_button)
    actions_layout.addWidget(logout_button)
    actions_layout.addStretch(1)
    layout.addRow("", actions)
    layout.addRow("Status:", status_label)
    refresh_status()

    layout.addItem(QVBoxLayout().addStretch())
    return widget

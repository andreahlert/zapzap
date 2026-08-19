"""Dialog to pick which chat to export and how many messages to include."""

from gettext import gettext as _

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from zapzap.ui.primitives import Button, CheckBox, ComboBox, Label, LineEdit


class ExportChatDialog(QDialog):
    """Collect the chat and history depth for a transcript export."""

    def __init__(self, chats, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Export chat"))
        self.setModal(True)
        self.resize(440, 540)

        self.selected_chat = None  # dict with id, name, isGroup
        self.selected_count = 100
        self.download_media = True

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(Label(
            _("Choose the chat and how much history to export as text."),
            "row_title",
            self,
        ))
        layout.addWidget(Label(
            _("Unofficial feature: it reads WhatsApp Web internals and "
              "may stop working after WhatsApp updates."),
            "row_description",
            self,
        ))

        self.filter_edit = LineEdit(parent=self)
        self.filter_edit.setPlaceholderText(_("Search chats…"))
        self.filter_edit.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter_edit)

        self.chat_list = QListWidget(self)
        for chat in chats:
            title = chat["name"]
            if chat.get("isGroup"):
                title = _("{} (group)").format(title)
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, chat)
            self.chat_list.addItem(item)
        self.chat_list.itemSelectionChanged.connect(self._refresh_export_button)
        self.chat_list.itemDoubleClicked.connect(lambda _item: self._confirm())
        layout.addWidget(self.chat_list, 1)

        self.depth_combo = ComboBox(self)
        for count, label in (
            (100, _("Last 100 messages")),
            (1000, _("Last 1000 messages")),
            (-1, _("Entire history (can take several minutes)")),
        ):
            self.depth_combo.addItem(label, count)
        layout.addWidget(self.depth_combo)

        self.media_check = CheckBox(
            _("Download media (images, audio, documents)"), self)
        self.media_check.setChecked(True)
        layout.addWidget(self.media_check)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_button = Button(_("Cancel"), parent=self)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        self.export_button = Button(_("Export"), Button.PRIMARY, parent=self)
        self.export_button.clicked.connect(self._confirm)
        buttons.addWidget(self.export_button)
        layout.addLayout(buttons)

        self._refresh_export_button()

    def _apply_filter(self, text):
        needle = text.strip().lower()
        for index in range(self.chat_list.count()):
            item = self.chat_list.item(index)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _refresh_export_button(self):
        self.export_button.setEnabled(
            self.chat_list.currentItem() is not None)

    def _confirm(self):
        item = self.chat_list.currentItem()
        if item is None:
            return
        self.selected_chat = item.data(Qt.ItemDataRole.UserRole)
        self.selected_count = self.depth_combo.currentData()
        self.download_media = self.media_check.isChecked()
        self.accept()

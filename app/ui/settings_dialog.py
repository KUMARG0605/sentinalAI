import os
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QMessageBox
from app.ui.state import load_app_state

class SettingsDialog(QDialog):
    def __init__(self, parent=None, on_save=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(500, 200)
        self.on_save = on_save
        self.state = load_app_state()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        lbl = QLabel("Model Locations")
        lbl.setStyleSheet("font-weight: bold; font-size: 11pt;")
        layout.addWidget(lbl)

        desc = QLabel("Update the paths to your local AI models below.")
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)
        layout.addSpacing(10)

        # Vosk path
        v_layout = QHBoxLayout()
        v_layout.addWidget(QLabel("Vosk Model Folder:"))
        self.vosk_input = QLineEdit(self.state.get("vosk_model_path", ""))
        v_layout.addWidget(self.vosk_input)
        v_btn = QPushButton("Browse...")
        v_btn.clicked.connect(lambda: self._browse(self.vosk_input))
        v_layout.addWidget(v_btn)
        layout.addLayout(v_layout)

        # Piper path
        p_layout = QHBoxLayout()
        p_layout.addWidget(QLabel("Piper Models Folder:"))
        self.piper_input = QLineEdit(self.state.get("piper_models_path", ""))
        p_layout.addWidget(self.piper_input)
        p_btn = QPushButton("Browse...")
        p_btn.clicked.connect(lambda: self._browse(self.piper_input))
        p_layout.addWidget(p_btn)
        layout.addLayout(p_layout)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

    def _browse(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            line_edit.setText(folder)

    def _save(self):
        v = self.vosk_input.text().strip()
        p = self.piper_input.text().strip()
        
        if v and not os.path.isdir(v):
            QMessageBox.warning(self, "Invalid Path", "Vosk path is not a valid directory.")
            return
        if p and not os.path.isdir(p):
            QMessageBox.warning(self, "Invalid Path", "Piper models path is not a valid directory.")
            return

        config = {
            "vosk_model_path": v,
            "piper_models_path": p
        }
        
        # Preserve existing exclude_paths from the parent's live state if available
        exclude_paths = []
        if self.parent() and hasattr(self.parent(), "state"):
            exclude_paths = self.parent().state.get("exclude_paths", [])
        else:
            exclude_paths = self.state.get("exclude_paths", [])

        if self.on_save:
            self.on_save(config, exclude_paths)
            
        self.accept()

import os
from pathlib import Path
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QFileDialog, QHBoxLayout, QMessageBox
from app.src.config import vosk_model_path, piper_executable_path
from app.ui.state import load_app_state, save_app_state

class DependencySetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SentinelAI - Required Models Setup")
        self.setModal(True)
        self.resize(500, 250)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        title = QLabel("Initial Setup: Voice Models")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(title)

        desc = QLabel("Please provide the paths to the required machine learning models for Voice functionality.\nThese are not bundled to save installer space.")
        layout.addWidget(desc)

        # Vosk path
        self.vosk_input = self._add_path_row(layout, "Vosk Speech Recognition Model Folder:", load_app_state().get("vosk_model_path", ""))
        
        # Piper models path
        self.piper_ext_input = self._add_path_row(layout, "Piper Models Folder:", load_app_state().get("piper_models_path", ""))

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        save_btn = QPushButton("Save && Continue")
        save_btn.clicked.connect(self._on_save)
        btn_box.addWidget(save_btn)
        layout.addLayout(btn_box)

    def _add_path_row(self, parent_layout, label_text, default_val):
        lbl = QLabel(label_text)
        parent_layout.addWidget(lbl)
        row = QHBoxLayout()
        inp = QLineEdit(default_val)
        row.addWidget(inp)
        btn = QPushButton("Browse...")
        btn.clicked.connect(lambda: self._browse_folder(inp))
        row.addWidget(btn)
        parent_layout.addLayout(row)
        return inp

    def _browse_folder(self, line_edit):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            line_edit.setText(folder)

    def _on_save(self):
        vosk = self.vosk_input.text().strip()
        piper = self.piper_ext_input.text().strip()
        
        if not vosk or not piper:
            QMessageBox.warning(self, "Validation Error", "Please provide both paths.")
            return
            
        if not os.path.isdir(vosk):
            QMessageBox.warning(self, "Invalid Path", f"Vosk path is not a valid directory:\n{vosk}")
            return
            
        if not os.path.isdir(piper):
            QMessageBox.warning(self, "Invalid Path", f"Piper models path is not a valid directory:\n{piper}")
            return

        state = load_app_state()
        state["vosk_model_path"] = vosk
        state["piper_models_path"] = piper
        save_app_state(state)
        self.accept()

def check_and_run_setup():
    """Checks if models are configured. If not, blocks and shows the setup dialog.
    Skipped entirely in --background mode — background process has no visible window.
    """
    import sys
    if "--background" in sys.argv:
        return

    state = load_app_state()
    vosk = state.get("vosk_model_path")
    piper = state.get("piper_models_path")

    needs_setup = False
    if not vosk or not os.path.isdir(vosk):
        needs_setup = True
    if not piper or not os.path.isdir(piper):
        needs_setup = True

    if needs_setup:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        created_app = False
        if not app:
            app = QApplication([])
            created_app = True

        dialog = DependencySetupDialog()
        if dialog.exec_() != QDialog.Accepted:
            sys.exit(0)

        if created_app:
            app.quit()
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from biography.gui import MainWindow, ReviewDialog, RolesDialog
from biography.schemas import FactSet, Transcript

FIXTURES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def test_desktop_shows_history_setup_and_input_errors(qt_app, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(
        "biography.gui.message", lambda window, text: errors.append(str(text))
    )
    w = MainWindow(tmp_path)
    w.show()
    qt_app.processEvents()
    assert w.tabs.count() == 3
    assert "не подготовлены" in w.setup_status.text()
    w.start_button.click()
    assert errors and w.process is None
    w.grab().save(str(tmp_path / "desktop.png"))
    w.close()


def test_role_confirmation_and_fact_review_widgets(qt_app):
    transcript = Transcript.model_validate_json(
        (FIXTURES / "transcript.json").read_text(encoding="utf-8")
    )
    dialog = RolesDialog(transcript)
    values = {
        "SPEAKER_00": "interviewer",
        "SPEAKER_01": "respondent",
        "SPEAKER_02": "third_party",
    }
    for speaker, combo in dialog.combos.items():
        combo.setCurrentIndex(combo.findData(values[speaker]))
    dialog.validate()
    assert dialog.result() == QDialog.Accepted
    facts = FactSet.model_validate_json(
        (FIXTURES / "facts_expected.json").read_text(encoding="utf-8")
    )
    editor = ReviewDialog(facts)
    editor.table.item(0, 2).setText("Проверенное значение")
    editor.approvals[0].setChecked(True)
    editor.save()
    assert (
        editor.facts.facts[0].value == "Проверенное значение"
        and editor.facts.facts[0].human_reviewed
    )
    assert facts.facts[0].value != "Проверенное значение"

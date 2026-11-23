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
    assert not w.start_button.isEnabled()
    assert not w.export_button.isEnabled()
    assert not w.log.isVisible()
    w.setup_link.click()
    assert w.tabs.currentWidget() is w.setup_page
    w.start_job()
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


def test_history_cannot_redirect_an_active_job_into_another_project(
    qt_app, tmp_path, monkeypatch
):
    errors = []
    monkeypatch.setattr(
        "biography.gui.message", lambda window, text: errors.append(str(text))
    )
    w = MainWindow(tmp_path)
    active = tmp_path / "active-project"
    w.current_output = active
    w.process = object()
    w.history_rows = [
        (
            "other",
            "date",
            "source",
            str(tmp_path / "other-project"),
            "name",
            "completed",
        )
    ]
    w.open_history(0, 0)
    w.import_job()
    assert w.current_output == active and len(errors) == 2
    w.process = None
    w.close()

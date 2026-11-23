"""Local desktop client. Long processing runs in cancellable isolated processes."""

import json
import os
import uuid
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .io import write_json
from .runtime import data_directory, process_command, resource_path
from .schemas import FactSet, Transcript
from .store import JobStore

STAGES = {
    "verification": "Проверка исходного файла и моделей",
    "audio_preparation": "Подготовка аудио",
    "diarization": "Разделение голосов",
    "transcription": "Распознавание речи",
    "llm_loading": "Загрузка языковой модели",
    "roles": "Определение ролей",
    "facts": "Извлечение сведений",
    "documents": "Создание документов",
}
STATUSES = {
    "completed": "Завершено — требуется проверка",
    "awaiting_roles": "Уточните говорящих",
    "running": "Обработка",
    "failed": "Ошибка",
    "cancelled": "Остановлено",
    "pending": "Ожидает",
}
ROLES = {
    "interviewer": "Интервьюер",
    "respondent": "Респондент",
    "third_party": "Третий участник",
    "unknown": "Не определён",
}


def message(window, text):
    QMessageBox.information(window, "Биография", str(text))


class PrepareThread(QThread):
    progress = Signal(str, int)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root
        self.cancelled = False

    def run(self):
        try:
            from .assets import prepare

            def progress(name, done, total):
                self.progress.emit(name, int(done * 100 / max(total, 1)))

            result = prepare(self.root, progress, lambda: self.cancelled)
            self.done.emit(str(result))
        except Exception as error:
            self.failed.emit(str(error))


class RolesDialog(QDialog):
    def __init__(self, transcript, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Уточните роли говорящих")
        self.resize(850, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Прослушайте примеры в основном окне и назначьте одного интервьюера и одного респондента."
            )
        )
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Голос", "Пример реплики", "Роль"])
        layout.addWidget(self.table)
        self.combos = {}
        for speaker in sorted(
            {s.speaker_id for s in transcript.segments if s.speaker_id}
        ):
            example = next(s for s in transcript.segments if s.speaker_id == speaker)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(speaker))
            self.table.setItem(row, 1, QTableWidgetItem(example.text_raw))
            combo = QComboBox()
            for value, label in ROLES.items():
                combo.addItem(label, value)
            combo.setCurrentIndex(max(0, combo.findData(example.speaker_role.value)))
            self.table.setCellWidget(row, 2, combo)
            self.combos[speaker] = combo
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def mapping(self):
        return {speaker: combo.currentData() for speaker, combo in self.combos.items()}

    def validate(self):
        roles = list(self.mapping().values())
        if (
            roles.count("respondent") != 1
            or roles.count("interviewer") != 1
            or "unknown" in roles
        ):
            message(
                self,
                "Назначьте ровно одного респондента и одного интервьюера. Остальному голосу назначьте роль третьего участника.",
            )
            return
        self.accept()


class TemplateDialog(QDialog):
    def __init__(self, paths, parent=None):
        super().__init__(parent)
        from .templates import FIELDS, analyze_samples

        self.blocks = analyze_samples(paths)
        self.setWindowTitle(
            "Настройка образца — персональные сведения не переносятся автоматически"
        )
        self.resize(1050, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Сопоставьте абзацы с полями. «Дословно» выбирайте только для текста без персональных сведений человека из образца."
            )
        )
        self.name = QLineEdit("Профиль биографии")
        layout.addWidget(self.name)
        self.table = QTableWidget(len(self.blocks), 3)
        self.table.setHorizontalHeaderLabels(
            [
                "Текст образца",
                "Поле результата",
                "Дословно: подтверждаю отсутствие чужих персональных сведений",
            ]
        )
        self.fields = []
        self.fixed = []
        for row, block in enumerate(self.blocks):
            item = QTableWidgetItem(block["sample_text"])
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            combo = QComboBox()
            for key, label in FIELDS.items():
                combo.addItem(label, key)
            combo.setCurrentIndex(combo.findData(block["field"]))
            self.table.setCellWidget(row, 1, combo)
            self.fields.append(combo)
            check = QCheckBox("Сохранить дословно")
            self.table.setCellWidget(row, 2, check)
            self.fixed.append(check)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choices(self):
        return [
            {
                "field": f.currentData(),
                "fixed": c.isChecked(),
                "fixed_confirmed": c.isChecked(),
            }
            for f, c in zip(self.fields, self.fixed)
        ]


class ReviewDialog(QDialog):
    def __init__(self, facts, parent=None):
        super().__init__(parent)
        self.facts = facts.model_copy(deep=True)
        self.setWindowTitle("Проверка людей и фактов")
        self.resize(1150, 700)
        from .documents import LABELS

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Проверяйте сведения по аудио. Цитаты сохраняются неизменными. Исправления будут сохранены отдельной версией."
            )
        )
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self.people = QTableWidget(len(facts.people), 3)
        self.people.setHorizontalHeaderLabels(["Лицо", "Имя", "Связь с респондентом"])
        for row, p in enumerate(facts.people):
            item = QTableWidgetItem(p.person_id)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.people.setItem(row, 0, item)
            self.people.setItem(row, 1, QTableWidgetItem(p.name or ""))
            self.people.setItem(
                row, 2, QTableWidgetItem(p.relationship_to_respondent or "")
            )
        tabs.addTab(self.people, "Люди")
        self.table = QTableWidget(len(facts.facts), 6)
        self.table.setHorizontalHeaderLabels(
            [
                "Человек",
                "Категория",
                "Сведения",
                "Определённость",
                "Цитаты",
                "Проверено",
            ]
        )
        self.subjects = []
        self.predicates = []
        self.certainties = []
        self.approvals = []
        people = {p.person_id: p for p in facts.people}
        for row, f in enumerate(facts.facts):
            subject = QComboBox()
            subject.addItem("Лицо не установлено", None)
            for p in people.values():
                subject.addItem(p.name or p.person_id, p.person_id)
            subject.setCurrentIndex(max(0, subject.findData(f.subject_person_id)))
            self.table.setCellWidget(row, 0, subject)
            self.subjects.append(subject)
            predicate = QComboBox()
            for value, label in LABELS.items():
                predicate.addItem(label, value)
            if predicate.findData(f.predicate) < 0:
                predicate.addItem("Другие сведения", f.predicate)
            predicate.setCurrentIndex(predicate.findData(f.predicate))
            self.table.setCellWidget(row, 1, predicate)
            self.predicates.append(predicate)
            self.table.setItem(row, 2, QTableWidgetItem(f.value))
            certainty = QComboBox()
            for value, label in [
                ("explicit", "Сообщено прямо"),
                ("approximate", "Приблизительно"),
                ("ambiguous", "Неоднозначно"),
            ]:
                certainty.addItem(label, value)
            certainty.setCurrentIndex(certainty.findData(f.certainty))
            self.table.setCellWidget(row, 3, certainty)
            self.certainties.append(certainty)
            quotes = QTableWidgetItem("\n".join(e.quote for e in f.evidence))
            quotes.setFlags(quotes.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 4, quotes)
            approved = QCheckBox()
            approved.setChecked(f.human_reviewed)
            self.table.setCellWidget(row, 5, approved)
            self.approvals.append(approved)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        tabs.addTab(self.table, "Факты")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        for row, p in enumerate(self.facts.people):
            p.name = self.people.item(row, 1).text().strip() or None
            p.relationship_to_respondent = (
                self.people.item(row, 2).text().strip() or None
            )
        for row, f in enumerate(self.facts.facts):
            f.subject_person_id = self.subjects[row].currentData()
            f.predicate = self.predicates[row].currentData()
            value = self.table.item(row, 2).text().strip()
            if not value:
                message(self, "Сведения не могут быть пустыми")
                return
            f.value = value
            f.certainty = self.certainties[row].currentData()
            f.human_reviewed = self.approvals[row].isChecked()
            if f.human_reviewed:
                f.requires_review = False
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, root=None):
        super().__init__()
        self.setWindowTitle("Биография — локальная обработка интервью")
        screen = QApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        self.resize(
            min(1180, area.width() - 40) if area else 1180,
            min(820, area.height() - 60) if area else 820,
        )
        self.preferences = QSettings("LocalBiography", "Biography")
        self.root = Path(
            root or self.preferences.value("data_root", str(data_directory()))
        )
        self.store = JobStore(self.root)
        self.current_output = None
        self.transcript = None
        self.process = None
        self.preparation = None
        self.cancel_requested = False
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        base = QWidget()
        self.setCentralWidget(base)
        layout = QVBoxLayout(base)
        heading = QLabel("Биография из интервью")
        heading.setStyleSheet("font-size:24px;font-weight:600")
        layout.addWidget(heading)
        layout.addWidget(
            QLabel(
                "Обработка аудио выполняется на этом компьютере. Готовые документы требуют проверки человеком."
            )
        )
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self._build_process_tab()
        self._build_history_tab()
        self._build_setup_tab()
        self.refresh_profiles()
        self.refresh_history()
        self.refresh_setup()

    def _build_process_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        audio_row = QHBoxLayout()
        self.audio_path = QLineEdit()
        self.audio_path.setPlaceholderText("Аудиофайл M4A, MP3 или WAV")
        self.audio_path.setObjectName("audioPath")
        audio_row.addWidget(self.audio_path)
        choose = QPushButton("Выбрать аудио")
        choose.clicked.connect(self.choose_audio)
        audio_row.addWidget(choose)
        sample = QPushButton("Синтетический пример")
        sample.clicked.connect(
            lambda: self.audio_path.setText(
                str(resource_path("examples/audio/interview.wav"))
            )
        )
        audio_row.addWidget(sample)
        layout.addLayout(audio_row)
        options = QHBoxLayout()
        options.addWidget(QLabel("Участников:"))
        self.speakers = QComboBox()
        self.speakers.addItems(["2", "3"])
        options.addWidget(self.speakers)
        options.addWidget(QLabel("Шаблон:"))
        self.profile = QComboBox()
        options.addWidget(self.profile, 1)
        import_template = QPushButton("Импорт DOCX-образца")
        import_template.clicked.connect(self.import_template)
        options.addWidget(import_template)
        layout.addLayout(options)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Начать обработку")
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_job)
        buttons.addWidget(self.start_button)
        self.cancel_button = QPushButton("Остановить")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_job)
        buttons.addWidget(self.cancel_button)
        self.roles_button = QPushButton("Уточнить говорящих")
        self.roles_button.clicked.connect(self.review_roles)
        buttons.addWidget(self.roles_button)
        self.resume_button = QPushButton("Повторить анализ")
        self.resume_button.clicked.connect(self.resume_job)
        buttons.addWidget(self.resume_button)
        layout.addLayout(buttons)
        self.status = QLabel("Готово к выбору аудио")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.transcript_table = QTableWidget(0, 3)
        self.transcript_table.setHorizontalHeaderLabels(["Время", "Говорящий", "Текст"])
        self.transcript_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self.transcript_table.itemSelectionChanged.connect(self.seek_segment)
        layout.addWidget(self.transcript_table, 1)
        playback = QHBoxLayout()
        play = QPushButton("Воспроизвести / пауза")
        play.clicked.connect(self.play_pause)
        playback.addWidget(play)
        revise = QPushButton("Исправить транскрипцию")
        revise.clicked.connect(self.edit_transcript)
        playback.addWidget(revise)
        facts = QPushButton("Проверить людей и факты")
        facts.clicked.connect(self.review_facts)
        playback.addWidget(facts)
        self.export_button = QPushButton("Создать DOCX")
        self.export_button.clicked.connect(self.export_job)
        playback.addWidget(self.export_button)
        files = QPushButton("Папка результатов")
        files.clicked.connect(self.open_output)
        playback.addWidget(files)
        layout.addLayout(playback)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(110)
        layout.addWidget(self.log)
        self.tabs.addTab(page, "Интервью")

    def _build_history_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по ФИО, файлу и дате")
        self.search.textChanged.connect(self.refresh_history)
        row.addWidget(self.search)
        imp = QPushButton("Открыть сохранённую папку")
        imp.clicked.connect(self.import_job)
        row.addWidget(imp)
        layout.addLayout(row)
        self.history = QTableWidget(0, 4)
        self.history.setHorizontalHeaderLabels(
            ["Дата", "ФИО / файл", "Состояние", "Папка"]
        )
        self.history.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.history.cellDoubleClicked.connect(self.open_history)
        layout.addWidget(self.history)
        self.tabs.addTab(page, "История")

    def _build_setup_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.folder_label = QLabel(str(self.root))
        self.folder_label.setWordWrap(True)
        layout.addWidget(self.folder_label)
        choose = QPushButton("Выбрать папку данных и моделей")
        choose.clicked.connect(self.choose_root)
        layout.addWidget(choose)
        layout.addWidget(
            QLabel(
                "Первичная подготовка требует около 5,2 ГБ загрузки и не менее 12 ГБ свободного места.\nПосле подготовки обработка работает без интернета. Рекомендуется 16 ГБ RAM.\nПапку данных можно перенести на другой компьютер для полностью офлайн-установки моделей."
            )
        )
        self.setup_status = QLabel()
        self.setup_status.setWordWrap(True)
        layout.addWidget(self.setup_status)
        self.prepare_button = QPushButton(
            "Подготовить локальные модели — требуется интернет"
        )
        self.prepare_button.clicked.connect(self.prepare_models)
        layout.addWidget(self.prepare_button)
        pause = QPushButton("Приостановить подготовку")
        pause.clicked.connect(self.pause_preparation)
        layout.addWidget(pause)
        self.setup_progress = QProgressBar()
        layout.addWidget(self.setup_progress)
        custom = QPushButton("Выбрать другие локальные модели")
        custom.clicked.connect(self.custom_models)
        layout.addWidget(custom)
        self.threads = QSpinBox()
        self.threads.setRange(1, max(1, os.cpu_count() or 1))
        self.threads.setValue(min(4, os.cpu_count() or 1))
        layout.addWidget(QLabel("Потоков CPU (применяется к новым заданиям):"))
        layout.addWidget(self.threads)
        layout.addStretch()
        self.tabs.addTab(page, "Подготовка и настройки")

    def custom_models(self):
        if self.process or (self.preparation and self.preparation.isRunning()):
            return
        from .cli import settings_at
        from .schemas import Settings
        from .sherpa_backend import load_config

        current = None
        try:
            current = settings_at(self.root / "settings.json")
        except (OSError, ValueError):
            pass
        values = {
            "asr": current.whisper_model if current else "",
            "llm": current.llm_model if current else "",
            "seg": "",
            "embedding": "",
        }
        if current and current.diarization_backend == "sherpa_onnx":
            try:
                c = load_config(Path(current.diarization_config))
                values.update(seg=c.segmentation_model, embedding=c.embedding_model)
            except (OSError, ValueError):
                pass
        dialog = QDialog(self)
        dialog.setWindowTitle("Локальные модели — без загрузки из сети")
        dialog.resize(900, 330)
        layout = QVBoxLayout(dialog)
        fields = {}
        labels = {
            "asr": "Папка Whisper в формате CTranslate2",
            "seg": "Segmentation ONNX для Sherpa",
            "embedding": "Embedding ONNX с метаданными Sherpa",
            "llm": "Языковая модель GGUF (первая часть, если их несколько)",
        }
        for key, title in labels.items():
            layout.addWidget(QLabel(title))
            row = QHBoxLayout()
            edit = QLineEdit(values[key])
            fields[key] = edit
            row.addWidget(edit)
            button = QPushButton("Выбрать")

            def choose(checked=False, key=key):
                if key == "asr":
                    selected = QFileDialog.getExistingDirectory(
                        dialog, "Модель Whisper"
                    )
                else:
                    selected, _ = QFileDialog.getOpenFileName(
                        dialog, "Локальная модель", "", "Модели (*.onnx *.gguf)"
                    )
                if selected:
                    fields[key].setText(selected)

            button.clicked.connect(choose)
            row.addWidget(button)
            layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            for key in ("seg", "embedding", "llm"):
                if not Path(fields[key].text()).is_file():
                    raise ValueError("Выберите существующие файлы моделей")
            if not (Path(fields["asr"].text()) / "model.bin").is_file():
                raise ValueError("В папке Whisper отсутствует model.bin")
            folder = self.root / "model-configs"
            folder.mkdir(exist_ok=True)
            path = folder / (uuid.uuid4().hex + ".json")
            write_json(
                path,
                {
                    "segmentation_model": str(Path(fields["seg"].text()).resolve()),
                    "embedding_model": str(Path(fields["embedding"].text()).resolve()),
                    "num_clusters": -1,
                    "cluster_threshold": 0.5,
                },
            )
            from .runtime import local_tool

            settings = Settings(
                whisper_model=str(Path(fields["asr"].text()).resolve()),
                diarization_config=str(path),
                diarization_backend="sherpa_onnx",
                llm_model=str(Path(fields["llm"].text()).resolve()),
                llm_context=8192,
                chunk_chars=12000,
                threads=self.threads.value(),
                ffmpeg=local_tool("ffmpeg"),
                ffprobe=local_tool("ffprobe"),
            )
            write_json(self.root / "settings.json", settings)
            self.refresh_setup()
        except Exception as error:
            message(self, error)

    def choose_audio(self):
        name, _ = QFileDialog.getOpenFileName(
            self, "Выберите интервью", "", "Аудио (*.m4a *.mp3 *.wav)"
        )
        if name:
            self.audio_path.setText(name)

    def choose_root(self):
        if self.process or (self.preparation and self.preparation.isRunning()):
            message(self, "Дождитесь завершения текущей операции")
            return
        name = QFileDialog.getExistingDirectory(self, "Папка данных", str(self.root))
        if not name:
            return
        self.store.close()
        self.root = Path(name)
        self.store = JobStore(self.root)
        self.preferences.setValue("data_root", name)
        self.folder_label.setText(name)
        self.current_output = None
        self.refresh_profiles()
        self.refresh_history()
        self.refresh_setup()

    def refresh_setup(self):
        path = self.root / "settings.json"
        if not path.exists():
            self.setup_status.setText(
                "Модели не подготовлены. Можно выбрать существующую папку данных или выполнить загрузку."
            )
            return
        try:
            from .cli import settings_at
            from .diagnostics import check_environment

            result = check_environment(settings_at(path))
            self.setup_status.setText(
                "Локальные компоненты найдены. Можно обрабатывать аудио."
                if not result["errors"]
                else "\n".join(result["errors"])
            )
        except Exception as error:
            self.setup_status.setText(str(error))

    def prepare_models(self):
        if self.process or (self.preparation and self.preparation.isRunning()):
            return
        self.prepare_button.setEnabled(False)
        self.preparation = PrepareThread(self.root, self)
        self.preparation.progress.connect(
            lambda name, percent: (
                self.setup_status.setText("Подготовка: " + name),
                self.setup_progress.setValue(percent),
            )
        )
        self.preparation.done.connect(lambda path: self.preparation_finished(None))
        self.preparation.failed.connect(self.preparation_finished)
        self.preparation.start()

    def pause_preparation(self):
        if self.preparation and self.preparation.isRunning():
            self.preparation.cancelled = True
            self.setup_status.setText(
                "Остановка загрузки; готовые файлы и частичные загрузки сохранятся."
            )

    def preparation_finished(self, error):
        self.prepare_button.setEnabled(True)
        self.refresh_setup()
        if error:
            self.setup_status.setText("Подготовка не завершена: " + error)
        else:
            self.setup_status.setText(
                "Подготовка завершена. Для обработки интернет не нужен."
            )
            self.setup_progress.setValue(100)

    def refresh_profiles(self):
        self.profile.clear()
        self.profile.addItem("Стандартная структура", None)
        for p in sorted((self.root / "profiles").glob("*/profile.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self.profile.addItem(
                    data["name"] + " · версия " + str(data["version"]), str(p)
                )
            except (OSError, ValueError, KeyError):
                continue

    def import_template(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Выберите один или несколько образцов", "", "DOCX (*.docx)"
        )
        if not paths:
            return
        try:
            dialog = TemplateDialog(paths, self)
            if dialog.exec() != QDialog.Accepted:
                return
            from .templates import create_profile

            folder = self.root / "profiles" / uuid.uuid4().hex
            name = dialog.name.text().strip() or "Профиль"
            old = []
            for p in (self.root / "profiles").glob("*/profile.json"):
                data = json.loads(p.read_text(encoding="utf-8"))
                if data["name"] == name:
                    old.append(data)
            profile = create_profile(
                [Path(p) for p in paths], dialog.choices(), folder, name
            )
            if old:
                previous = max(old, key=lambda d: d["version"])
                data = json.loads(profile.read_text(encoding="utf-8"))
                data["version"] = previous["version"] + 1
                data["previous_profile_id"] = previous["profile_id"]
                write_json(profile, data)
            self.refresh_profiles()
            self.profile.setCurrentIndex(self.profile.findData(str(profile)))
        except Exception as error:
            message(self, error)

    def _settings_for_job(self, job_id):
        from .cli import settings_at
        from .diagnostics import check_environment
        from .sherpa_backend import load_config

        settings = settings_at(self.root / "settings.json")
        settings.threads = self.threads.value()
        errors = check_environment(settings)["errors"]
        if errors:
            raise ValueError("\n".join(errors))
        folder = self.root / "job-configs"
        folder.mkdir(exist_ok=True)
        if settings.diarization_backend == "sherpa_onnx":
            c = load_config(Path(settings.diarization_config))
            c.num_clusters = int(self.speakers.currentText())
            write_json(folder / (job_id + ".diarization.json"), c)
            settings.diarization_config = str(folder / (job_id + ".diarization.json"))
        path = folder / (job_id + ".json")
        write_json(path, settings)
        return path

    def start_job(self):
        source = Path(self.audio_path.text())
        if not source.is_file():
            message(self, "Выберите существующий аудиофайл")
            return
        self._launch(source, False, None)

    def _launch(self, source, resume, roles, cache_from=None):
        if self.process:
            return
        try:
            job_id = uuid.uuid4().hex
            config = self._settings_for_job(job_id)
            output = self.root / "jobs" / job_id
            command = [
                "analyze" if resume else "run",
                str(source),
                "--config",
                str(config),
                "--output",
                str(output),
            ]
            if roles:
                command += ["--roles", str(roles)]
            if cache_from:
                command += ["--resume-project", str(cache_from)]
            profile = self.profile.currentData()
            if profile:
                command += ["--template-profile", profile]
            self.current_output = output
            self.store.add(job_id, source, output, "running")
            self.log.clear()
            self.cancel_requested = False
            self.process = QProcess(self)
            self.process.setProgram(process_command()[0])
            self.process.setArguments(process_command()[1:] + command)
            self.process.readyReadStandardOutput.connect(self.read_stdout)
            self.process.readyReadStandardError.connect(self.read_stderr)
            self.process.finished.connect(self.job_finished)
            self.process.errorOccurred.connect(self.process_error)
            self.start_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.progress.setRange(0, 0)
            self.status.setText("Обработка начата. Все этапы выполняются локально.")
            self.process.start()
            self.refresh_history()
        except Exception as error:
            message(self, error)
            self.tabs.setCurrentIndex(2)

    def process_error(self, error):
        if self.process and error == QProcess.FailedToStart:
            self.log.appendPlainText(
                "Не удалось запустить обработку: " + self.process.errorString()
            )
            self.job_finished(1, QProcess.CrashExit)

    def read_stdout(self):
        if not self.process:
            return
        text = bytes(self.process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        for line in text.splitlines():
            self.status.setText(STAGES.get(line, line))
            self.log.appendPlainText(STAGES.get(line, line))

    def read_stderr(self):
        if self.process:
            self.log.appendPlainText(
                bytes(self.process.readAllStandardError()).decode(
                    "utf-8", errors="replace"
                )
            )

    def cancel_job(self):
        if not self.process:
            return
        self.cancel_requested = True
        import psutil

        try:
            children = psutil.Process(self.process.processId()).children(recursive=True)
            for process in reversed(children):
                try:
                    process.kill()
                except psutil.Error:
                    pass
        except psutil.Error:
            pass
        self.process.kill()

    def job_finished(self, code, status):
        if self.process is None or (
            self.sender() is not None and self.sender() is not self.process
        ):
            return
        process = self.process
        self.process = None
        if process:
            process.deleteLater()
        self.start_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if code == 0 else 0)
        report_path = self.current_output / "quality_report.json"
        report = (
            json.loads(report_path.read_text(encoding="utf-8"))
            if report_path.exists()
            else {}
        )
        if self.cancel_requested:
            report["status"] = "cancelled"
            for value in report.get("stages", {}).values():
                if value.get("status") == "running":
                    value["status"] = "cancelled"
            write_json(report_path, report)
        elif report.get("status") == "running" or not report:
            report["status"] = "failed"
            report["error"] = "Обработка прервана или завершилась аварийно"
            write_json(report_path, report)
        state = report.get("status", "failed")
        name = None
        try:
            from .documents import build_sections

            t = Transcript.model_validate_json(
                (self.current_output / "transcript.json").read_text(encoding="utf-8")
            )
            f = FactSet.model_validate_json(
                (self.current_output / "facts.json").read_text(encoding="utf-8")
            )
            name = build_sections(f, t)[0]["full_name"]
        except (OSError, ValueError):
            pass
        self.store.update(self.current_output, state, name)
        self.status.setText(
            STATUSES.get(state, state)
            + ((": " + report["error"]) if report.get("error") else "")
        )
        self.refresh_history()
        self.load_project(self.current_output)
        if state == "awaiting_roles":
            QTimer.singleShot(0, self.review_roles)

    def refresh_history(self, *args):
        if not hasattr(self, "history"):
            return
        rows = self.store.list(self.search.text())
        self.history.setRowCount(len(rows))
        self.history_rows = rows
        for row, record in enumerate(rows):
            for col, value in enumerate(
                [
                    record[1],
                    record[4] or Path(record[2]).name,
                    STATUSES.get(record[5], record[5]),
                    record[3],
                ]
            ):
                self.history.setItem(row, col, QTableWidgetItem(str(value)))

    def open_history(self, row, col):
        self.load_project(Path(self.history_rows[row][3]))
        self.tabs.setCurrentIndex(0)

    def import_job(self):
        path = QFileDialog.getExistingDirectory(self, "Папка сохранённого интервью")
        if not path:
            return
        folder = Path(path)
        if (
            not (folder / "transcript.json").exists()
            and not (folder / "quality_report.json").exists()
        ):
            message(self, "В папке нет сохранённого интервью")
            return
        self.store.add(uuid.uuid4().hex, "Импортированное интервью", folder, "imported")
        self.load_project(folder)
        self.refresh_history()
        self.tabs.setCurrentIndex(0)

    def load_project(self, folder):
        self.current_output = Path(folder)
        metadata = self.current_output / "source.json"
        if metadata.is_file():
            self.audio_path.setText(
                json.loads(metadata.read_text(encoding="utf-8"))["path"]
            )
        path = self.current_output / "transcript.json"
        self.transcript = None
        if path.exists():
            self.transcript = Transcript.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        self.transcript_table.setRowCount(
            len(self.transcript.segments) if self.transcript else 0
        )
        if self.transcript:
            for row, s in enumerate(self.transcript.segments):
                for col, value in enumerate(
                    [
                        f"{s.start_ms // 60000:02d}:{s.start_ms // 1000 % 60:02d}",
                        ROLES[s.speaker_role.value]
                        + (" · проверить" if s.requires_review else ""),
                        s.text_raw,
                    ]
                ):
                    item = QTableWidgetItem(value)
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.transcript_table.setItem(row, col, item)
        wave = self.current_output / "prepared.wav"
        if not wave.exists() and (self.current_output / "audio_link.json").is_file():
            wave = Path(
                json.loads(
                    (self.current_output / "audio_link.json").read_text(
                        encoding="utf-8"
                    )
                )["path"]
            )
        if not wave.exists():
            metadata = self.current_output / "source.json"
            if metadata.exists():
                wave = Path(json.loads(metadata.read_text(encoding="utf-8"))["path"])
        if wave.exists():
            self.player.setSource(QUrl.fromLocalFile(str(wave)))
        report = self.current_output / "quality_report.json"
        if report.exists():
            data = json.loads(report.read_text(encoding="utf-8"))
            self.status.setText(
                STATUSES.get(data.get("status"), data.get("status", ""))
            )
        if report.exists() and data.get("status") == "completed":
            self.progress.setRange(0, 100)
            self.progress.setValue(100)
        self.transcript_table.resizeRowsToContents()
        self.roles_button.setEnabled(bool(self.transcript))
        self.resume_button.setEnabled(
            bool(self.transcript) or (self.current_output / "source.json").is_file()
        )
        self.export_button.setEnabled((self.current_output / "facts.json").exists())

    def seek_segment(self):
        row = self.transcript_table.currentRow()
        if self.transcript and 0 <= row < len(self.transcript.segments):
            self.player.setPosition(self.transcript.segments[row].start_ms)

    def play_pause(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def review_roles(self):
        if (
            self.process
            or not self.current_output
            or not (self.current_output / "transcript.json").exists()
        ):
            return
        transcript = Transcript.model_validate_json(
            (self.current_output / "transcript.json").read_text(encoding="utf-8")
        )
        dialog = RolesDialog(transcript, self)
        if dialog.exec() != QDialog.Accepted:
            return
        path = self.current_output / "roles_user_confirmed.json"
        write_json(path, dialog.mapping())
        self.resume_job(path)

    def resume_job(self, roles=None):
        if not self.current_output:
            message(self, "Выберите сохранённое задание в истории")
            return
        if not (self.current_output / "transcript.json").exists():
            metadata = self.current_output / "source.json"
            if metadata.exists():
                source = Path(json.loads(metadata.read_text(encoding="utf-8"))["path"])
                if source.is_file():
                    self._launch(source, False, None, self.current_output)
                    return
            message(
                self,
                "Для этого этапа нужно исходное аудио. Выберите его и запустите новое задание.",
            )
            return
        source = self.current_output / "transcript.json"
        if roles is False:
            roles = None
        if (
            roles is None
            and (self.current_output / "roles_user_confirmed.json").exists()
        ):
            roles = self.current_output / "roles_user_confirmed.json"
        self._launch(source, True, roles)

    def edit_transcript(self):
        if (
            self.process
            or not self.current_output
            or not (self.current_output / "transcript.json").exists()
        ):
            return
        transcript = Transcript.model_validate_json(
            (self.current_output / "transcript.json").read_text(encoding="utf-8")
        )
        dialog = QDialog(self)
        dialog.setWindowTitle("Исправления по аудио — исходная версия сохраняется")
        dialog.resize(1000, 650)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(transcript.segments), 2)
        table.setHorizontalHeaderLabels(["Время", "Текст"])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for row, s in enumerate(transcript.segments):
            table.setItem(row, 0, QTableWidgetItem(str(s.start_ms // 1000)))
            table.setItem(row, 1, QTableWidgetItem(s.text_raw))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        for row, s in enumerate(transcript.segments):
            value = table.item(row, 1).text().strip()
            if not value:
                message(self, "Реплика не может быть пустой")
                return
            s.text_raw = value
        folder = self.current_output / "revisions"
        folder.mkdir(exist_ok=True)
        path = folder / (uuid.uuid4().hex + ".transcript.json")
        write_json(path, transcript)
        self._launch(
            path,
            True,
            self.current_output / "roles_user_confirmed.json"
            if (self.current_output / "roles_user_confirmed.json").exists()
            else None,
        )

    def review_facts(self):
        if (
            self.process
            or not self.current_output
            or not (self.current_output / "facts.json").exists()
        ):
            message(self, "Сначала дождитесь извлечения фактов")
            return
        facts = FactSet.model_validate_json(
            (self.current_output / "facts.json").read_text(encoding="utf-8")
        )
        dialog = ReviewDialog(facts, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            from .intelligence import validate_sources

            validate_sources(dialog.facts, self.transcript.segments)
            folder = self.current_output / "revisions"
            folder.mkdir(exist_ok=True)
            path = folder / (uuid.uuid4().hex + ".facts.json")
            write_json(path, dialog.facts)
            write_json(self.current_output / "selected_facts.json", {"path": str(path)})
            self.export_job()
        except Exception as error:
            message(self, error)

    def export_job(self):
        if not self.current_output:
            return
        try:
            from .documents import export_documents

            source = self.current_output / "facts.json"
            selected = self.current_output / "selected_facts.json"
            if selected.exists():
                source = Path(json.loads(selected.read_text(encoding="utf-8"))["path"])
            transcript = Transcript.model_validate_json(
                (self.current_output / "transcript.json").read_text(encoding="utf-8")
            )
            facts = FactSet.model_validate_json(source.read_text(encoding="utf-8"))
            folder = self.current_output / "exports" / uuid.uuid4().hex
            result = export_documents(
                transcript, facts, folder, self.profile.currentData()
            )
            self.last_export = Path(result["biography"])
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
            message(
                self,
                "Созданы биография и две транскрипции. Проверьте выделенные сведения по аудио.",
            )
        except Exception as error:
            message(self, error)

    def open_output(self):
        if self.current_output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_output)))

    def closeEvent(self, event):
        if self.process:
            message(self, "Остановите обработку перед закрытием приложения.")
            event.ignore()
            return
        if self.preparation and self.preparation.isRunning():
            message(self, "Дождитесь подготовки моделей перед закрытием приложения.")
            event.ignore()
            return
        self.store.close()
        event.accept()


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


def smoke_gui(root, project, output):
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    window = MainWindow(root)
    window.show()
    window.load_project(project)
    window._settings_for_job("native-gui-preflight")
    for _ in range(40):
        app.processEvents()
        QTest.qWait(100)
        if window.player.duration() > 0:
            break
    if window.transcript_table.rowCount() == 0:
        raise ValueError("Frozen GUI did not load transcript")
    if window.player.duration() <= 0:
        raise ValueError("Frozen GUI did not decode local audio")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(output)):
        raise ValueError("Cannot save native GUI image")
    write_json(
        output.with_suffix(".json"),
        {
            "platform": app.platformName(),
            "transcript_rows": window.transcript_table.rowCount(),
            "audio_duration_ms": window.player.duration(),
            "screenshot_saved": True,
        },
    )
    window.close()
    return 0

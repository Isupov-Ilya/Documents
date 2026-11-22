# Биография из интервью — исследовательский прототип

Первый этап из раздела 15 ТЗ: аудио → Whisper → диаризация → роли → люди, связи и факты с цитатами → JSON и отчёт. [Исходное ТЗ](docs/specification.ru.md) сохранено дословно; [уточнения](docs/decisions.ru.md) ведутся отдельно.

**Статус:** конвейер реализован и проверен с тестовыми адаптерами. Нейросетевые адаптеры требуют испытаний с весами. Работа на Windows и качество моделей ещё не подтверждены. GUI, DOCX и история относятся к следующим этапам ТЗ.

## Проверка без моделей

Python 3.11 или 3.12. Из корня проекта:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m biography demo --output results/demo-001
```

В Linux используйте python3 и .venv/bin/python. Аудиотестам необходим FFmpeg; без него они явно пропускаются. Demo использует заранее размеченные ответы, а не нейросети.

## Настоящее аудио

Подготовьте [офлайн-установку Windows](docs/offline-windows.ru.md). В копии examples/settings.cpu.json укажите локальные пути: относительные пути моделей считаются от папки настроек.

```powershell
python -m biography check --config examples/settings.cpu.json
python -m biography run D:\Audio\interview.m4a --config examples/settings.cpu.json --output results/interview-001
```

Папка результатов должна быть новой. Сохраняются prepared.wav, source.json, settings.json, diarization.json, asr_raw.json, transcript.json, roles_proposed.json, facts.json и quality_report.json. Текст — исходный распознанный, без обещания буквальной точности. Очищенная транскрипция и DOCX пока не создаются.

CPU по умолчанию. Для NVIDIA: device=cuda, compute_type=float16, llm_gpu_layers=-1; требуются совместимые CUDA-сборки трёх адаптеров. Эти настройки не означают проверенную поддержку GPU.

Смешанные реплики не получают произвольного говорящего. Для уточнения ролей создайте JSON-файл:

```json
{"SPEAKER_00": "interviewer", "SPEAKER_01": "respondent", "SPEAKER_02": "third_party"}
```

После проверки прослушиванием повторите только анализ:

```powershell
python -m biography analyze results/interview-001/transcript.json --config examples/settings.cpu.json --roles my-roles.json --output results/interview-001-reviewed
```

Ctrl+C сохраняет завершённые файлы и отмечает текущий этап как cancelled. Полное автоматическое возобновление произвольного этапа пока не реализовано; готовую транскрипцию можно анализировать повторно.

## Проверка результатов

У каждого факта есть лицо (или null), источники и буквальные цитаты, приблизительность даты, тип свидетельства и флаг проверки. Противоречащие утверждения сохраняются вместе. Совпадение имён не является правилом объединения людей. [JSON Schema](docs/schemas) также экспортируются командой biography schemas --output new-directory.

Проверка вхождения цитат **не доказывает правильность интерпретации**. reported означает «сказано в интервью», а не «независимо проверено». Отчёт выделяет кандидаты повторов и расхождений отдельных атрибутов; решение принимает человек.

[Архитектура и ограничения](docs/architecture.ru.md) · [Проверки](docs/validation.ru.md)

# Офлайн-установка Windows

Требуются Windows x64, Python 3.11 (исходный код также поддерживает 3.12), FFmpeg/FFprobe и локальные веса трёх моделей. Минимальная память ещё не измерена. Результаты содержат персональные данные: ограничьте доступ к рабочей папке.

Зависимости ядра указаны в pyproject.toml. Нейросетевые extras: asr, diarization, llm. requirements/model-candidates.txt — кандидат для экспериментов, **не проверенный lock-файл**. Torch и torchaudio устанавливаются одинаковой версии; CPU/CUDA-сборки согласуются с CTranslate2 и llama.cpp.

## Подготовка с интернетом

Собирайте комплект на Windows той же архитектуры и версии Python. Linux wheels не подходят Windows. Получите официальный Python, доверенную сборку FFmpeg и необходимые права использования моделей. При отсутствии готового wheel для llama-cpp-python нужны Visual Studio Build Tools C++ и CMake. Соберите wheel до переноса на офлайн-машину.

Из корня проекта, PowerShell:

```powershell
py -3.11 -m venv .bundle-venv
.\.bundle-venv\Scripts\python.exe -m pip install --upgrade pip wheel
.\.bundle-venv\Scripts\python.exe -m pip wheel --wheel-dir wheelhouse ".[test]"
.\.bundle-venv\Scripts\python.exe -m pip wheel --wheel-dir wheelhouse -r requirements/model-candidates.txt
```

По умолчанию собирается CPU-вариант. До распространения установите комплект в чистую среду и проведите реальный прогон. Сохраните точный pip freeze, версии FFmpeg/CUDA/драйвера, ревизии моделей и SHA-256 файлов. Сравнивайте с опубликованными издателем суммами/подписями; собственный хеш подтверждает неизменность при переносе, но не доверие источнику. Не отключайте TLS и проверки целостности.

Переносите wheelhouse, Python installer, FFmpeg, модели, настройки и документацию/исходный код. Не переносите токены доступа. Учитывайте лицензии моделей и бинарных зависимостей.

## Кандидаты моделей

- ASR: multilingual Whisper в формате CTranslate2, например Systran/faster-whisper-small. Tiny годится для технического smoke-теста, но не выбора финального качества. Переносите полный snapshot, включая model.bin, config.json, tokenizer.json и словари. Адаптер использует local_files_only=True.
- Диаризация: pyannote.audio 3.3.2, локальные checkpoint pyannote/segmentation-3.0 и совместимой embedding-модели, например pyannote/embedding. Условия доступа и лицензии проверяются у владельцев. В копии examples/diarization.config.example.yaml укажите абсолютные пути к локальным файлам. Параметры кластеризации — экспериментальные. Удалённые Hub-ID в конфигурации не делают модель офлайн.
- LLM: GGUF instruction-модель с русским языком и chat template, поддерживаемым llama.cpp. Начальные кандидаты — Qwen2.5-3B-Instruct или 7B-Instruct Q4_K_M из доверенного источника. Не используйте base вместо Instruct. RAM, контекст и разрешение местоимений предстоит измерить.

Загрузка разрешена пользователем для подготовки и проверки; команды run/analyze сами не скачивают компоненты. Финальные модели выбираются после измерений.

## Установка без интернета

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-index --find-links E:\bundle\wheelhouse "local-biography[test]"
.\.venv\Scripts\python.exe -m pip install --no-index --find-links E:\bundle\wheelhouse -r requirements/model-candidates.txt
```

Добавьте каталог ffmpeg.exe/ffprobe.exe в PATH. Скопируйте модели и настройте пути в JSON. Настройки читаются как UTF-8 без BOM. По умолчанию examples/settings.cpu.json ищет модели в models репозитория.

```powershell
.\.venv\Scripts\python.exe -m biography check --config examples/settings.cpu.json
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m biography run D:\Audio\test.m4a --config examples/settings.cpu.json --output results/real-test-001
```

check проверяет наличие, но не совместимость/качество. Коды выхода: 0 — успех команды, 1 — ошибка, 2 — отсутствующие компоненты в check либо ошибка аргументов, 130 — Ctrl+C. Папка результатов должна быть новой.

Приёмка: прогон с отключённой сетью, размеченное реальное интервью, WER/DER, точность ролей, полнота фактов, привязка к людям, неопределённые даты и противоречия. Отдельно проверить 60 минут, 8 ГБ RAM, RTX 3070 Laptop и чистую Windows. До этих испытаний приложение не считается готовым к приёмке.

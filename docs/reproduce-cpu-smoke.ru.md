# Воспроизведение эксперимента на CPU

Это инструкция для разработчика по историческому Linux-эксперименту. Она не заменяет [установку готового Windows-приложения](offline-windows.ru.md). Нативный Windows-прогон с установленным EXE, реальными моделями и Windows Firewall теперь выполняется отдельно; доказательства прикладываются к выпускам GitHub.

Только подготовительные scripts/ обращаются в сеть. Рабочие команды run/analyze загружают файлы с диска и выполняются под запретом Python-сокетов. Это не системный сетевой sandbox: приёмочный прогон с физически отключённой сетью Windows ещё необходим.

Подготовка из корня репозитория:

```bash
python -m pip install -r requirements/smoke-cpu.txt
python -m pip install -e .
python scripts/prepare_models.py --output models
python scripts/prepare_embedding.py models/embedding/voxceleb_resnet34_LM.onnx --output models/embedding/voxceleb_resnet34_LM.sherpa.onnx
```

Для Linux-сборки llama-cpp-python здесь потребовались CC=gcc, CXX=g++, CMAKE_BUILD_PARALLEL_LEVEL=4, CMAKE_ARGS='-DGGML_NATIVE=OFF -DGGML_BLAS=OFF -DGGML_CUDA=OFF'. На Windows используйте поддерживаемый MSVC toolchain. requirements/smoke-cpu.txt фиксирует верхнеуровневые версии фактически испытанной Linux-среды, а не гарантирует установку Windows или полную блокировку транзитивных версий.

prepare_models.py фиксирует ревизии и сверяет LFS SHA-256 либо Git blob SHA-1 с метаданными издателя. Неверная загрузка не заменяет прежний файл. Повторный запуск проверяет и использует готовые файлы. Download manifest сохраняет источники и контрольные суммы. Скрипт не пересматривает лицензионные условия: перед дальнейшим использованием/распространением читайте сохранённые MODEL_CARD, README и LICENSE.

Поддерживаемые группы --only: asr (tiny), asr-small, diarization, llm, tts. По умолчанию скачиваются все экспериментальные компоненты (порядка 3 ГБ). Голоса denis и dmitri: в карточках наборов данных указан CC0. Неизвестные по лицензии голоса в сценарии не используются. GGUF взят из официального Qwen-репозитория; его отдельная лицензия сохранена рядом с весами.

Преобразование WeSpeaker добавляет только служебные метаданные по инструкции Sherpa. Исходный проверенный ONNX не изменяется. Производный файл имеет отдельную SHA-256 и derivation.json с происхождением. Это не подмена ожидаемой контрольной суммы исходных весов.

Создайте папку models/diarization-onnx и скопируйте туда examples/diarization.onnx.example.json под именем config.json. В Windows используйте New-Item/Copy-Item, в Linux mkdir/cp.

```bash
python scripts/synthesize_interview.py --models models/piper --output results/synthetic-audio-new
python -m biography check --config examples/settings.smoke-small.cpu.json
python -m biography run results/synthetic-audio-new/interview.wav --config examples/settings.smoke-small.cpu.json --output results/small-new
```

Число голосов в экспериментальном ONNX-конфиге принудительно равно двум — оно известно из сценария. Это не подтверждение автоматического подсчёта голосов. Для неопределённого количества используйте num_clusters=-1 и отдельно оцените результат; выход за min_speakers/max_speakers выдаёт ошибку.

```bash
python scripts/evaluate_smoke.py --reference results/synthetic-audio-new/reference.json --run results/small-new --output results/small-new/audio_metrics.json
```

Если модель ошиблась в ролях, предлагаемые назначения сохраняются, а недостоверные превращаются в unknown/requires_review. Для отдельного эксперимента с фактами разрешено передать известные из синтетического сценария роли:

```bash
python -m biography analyze results/small-new/transcript.json --config examples/settings.smoke-small.cpu.json --roles examples/roles.confirmed.json --output results/small-facts-new
```

**Эта команда использует внешнюю разметку ролей.** Она не доказывает успех автоматического определения ролей. В отчёте roles_source=provided_mapping. Готовые роли не вычисляются LLM заново. Для настоящего интервью такой файл нужно проверить прослушиванием.

Ошибки нативного ONNX-процесса перехватываются родителем и сохраняются как failed. Невалидные промежуточные факты сохраняются отдельно как facts_batch_*.json для диагностики; их нельзя считать проверенными facts.json.

WER здесь сравнивает регистр/пунктуацию-нормализованные слова без раскрытия числительных; подсчёт на одном коротком синтетическом примере не даёт доверительного интервала качества. Диагностика голоса проверяет центры TTS-контейнеров, которые могут попадать в паузы. Это не DER. Для приёмки нужен независимый размеченный корпус реальных интервью.

import argparse
import json
import sys
from pathlib import Path

from .io import write_json
from .schemas import FactSet, Role, RoleResult, Settings, Transcript


def settings_at(path):
    path = path.resolve(strict=True)
    settings = Settings.model_validate_json(path.read_text(encoding="utf-8"))
    for field in ("whisper_model", "diarization_config", "llm_model"):
        value = Path(getattr(settings, field)).expanduser()
        setattr(settings, field, str((path.parent / value).resolve()))
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Локальный исследовательский прототип интервью → факты"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "analyze"):
        p = sub.add_parser(
            command,
            help="Обработка аудио"
            if command == "run"
            else "Повторный анализ сохранённой транскрипции",
        )
        p.add_argument("input", type=Path)
        p.add_argument("--config", type=Path, required=True)
        p.add_argument(
            "--output",
            type=Path,
            required=True,
            help="Новая папка; существующая не перезаписывается",
        )
        p.add_argument(
            "--roles", type=Path, help="JSON: ID говорящего → подтверждённая роль"
        )
    p = sub.add_parser(
        "check", help="Проверка локальных зависимостей без загрузки моделей"
    )
    p.add_argument("--config", type=Path, required=True)
    p = sub.add_parser("demo", help="Размеченный пример БЕЗ запуска моделей")
    p.add_argument("--fixtures", type=Path, default=Path("examples"))
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("schemas", help="Экспорт JSON Schema")
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            from .diagnostics import check_environment

            result = check_environment(settings_at(args.config))
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result["errors"] else 0
        elif args.command == "schemas":
            args.output.mkdir(parents=True, exist_ok=False)
            for model in (Settings, Transcript, FactSet, RoleResult):
                write_json(
                    args.output / (model.__name__ + ".schema.json"),
                    model.model_json_schema(),
                )
        elif args.command == "demo":
            from .demo import run_demo

            run_demo(args.fixtures, args.output)
        else:
            settings = settings_at(args.config)
            confirmed = None
            if args.roles:
                data = json.loads(args.roles.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Файл ролей должен содержать объект JSON")
                confirmed = {k: Role(v) for k, v in data.items()}
            from .pipeline import analyze_saved, run_audio

            function = run_audio if args.command == "run" else analyze_saved
            function(
                args.input.resolve(strict=True),
                settings,
                args.output.resolve(),
                confirmed,
            )
        return 0
    except KeyboardInterrupt:
        print(
            "Остановлено пользователем. Завершённые этапы сохранены.", file=sys.stderr
        )
        return 130
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

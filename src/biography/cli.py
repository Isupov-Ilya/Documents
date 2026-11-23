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
    if getattr(sys, "frozen", False):
        from .runtime import local_tool

        settings.ffmpeg = local_tool("ffmpeg")
        settings.ffprobe = local_tool("ffprobe")
    return settings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Локальный исследовательский прототип интервью → факты"
    )
    parser.add_argument("--version", action="version", version="Biography 0.2.1")
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
        p.add_argument("--template-profile", type=Path)
        p.add_argument("--resume-project", type=Path)
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
    p = sub.add_parser("export", help="Создать DOCX из сохранённого интервью")
    p.add_argument("project", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--template-profile", type=Path)
    p = sub.add_parser(
        "prepare-assets", help="Явная первичная загрузка локальных моделей"
    )
    p.add_argument("--data-directory", type=Path, required=True)
    sub.add_parser("gui", help="Открыть графический интерфейс")
    p = sub.add_parser("gui-smoke", help=argparse.SUPPRESS)
    p.add_argument("--data-directory", type=Path, required=True)
    p.add_argument("--project", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    sub.add_parser("network-probe", help="Контрольная проверка сети для испытаний")
    p = sub.add_parser("schemas", help="Экспорт JSON Schema")
    p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "gui-smoke":
            from .gui import smoke_gui

            return smoke_gui(args.data_directory, args.project, args.output)
        elif args.command == "gui":
            from .gui import main as gui_main

            return gui_main()
        elif args.command == "prepare-assets":
            from .assets import prepare

            prepare(
                args.data_directory,
                lambda name, done, total: print(name, done, total, flush=True),
            )
            return 0
        elif args.command == "export":
            from .documents import export_documents
            from .intelligence import validate_sources
            from .offline import offline_mode

            transcript = Transcript.model_validate_json(
                (args.project / "transcript.json").read_text(encoding="utf-8")
            )
            facts = FactSet.model_validate_json(
                (args.project / "facts.json").read_text(encoding="utf-8")
            )
            args.output.mkdir(parents=True, exist_ok=False)
            with offline_mode():
                export_documents(
                    transcript,
                    validate_sources(facts, transcript.segments),
                    args.output,
                    args.template_profile,
                )
            return 0
        elif args.command == "network-probe":
            import socket

            try:
                with socket.create_connection(("1.1.1.1", 443), timeout=5):
                    print("NETWORK_REACHABLE")
                    return 0
            except OSError:
                print("NETWORK_BLOCKED")
                return 4
        elif args.command == "check":
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
            parameters = [
                args.input.resolve(strict=True),
                settings,
                args.output.resolve(),
                confirmed,
                args.template_profile,
            ]
            if args.command == "run":
                parameters.append(args.resume_project)
            function(*parameters)
        return 0
    except KeyboardInterrupt:
        print(
            "Остановлено пользователем. Завершённые этапы сохранены.", file=sys.stderr
        )
        return 130
    except Exception as error:
        from .pipeline import NeedsRoleConfirmation

        print(f"Ошибка: {error}", file=sys.stderr)
        return 3 if isinstance(error, NeedsRoleConfirmation) else 1


if __name__ == "__main__":
    sys.exit(main())

# ae-video

Плагин Claude Code для видео в After Effects: запись голоса, транскрипт, план анимации, сборка из базы, предревью, доводка и обновление базы. Работает локально на Mac.

## Установка

Нужны Apple Silicon, Python 3.9+, ffmpeg и After Effects.

```bash
pip3 install -r requirements.txt
```

```bash
claude plugin marketplace add /Users/bogdan/claude/agents/audio_to_transcript_agent
```

```bash
claude plugin install ae-video@ae-video-local
```

```bash
python3 plugin/scripts/doctor.py
```

В After Effects включите Settings > Scripting & Expressions > Allow Scripts to Write Files and Access Network.

Для разработки плагин можно подключить на одну сессию:

```bash
claude --plugin-dir ./plugin
```

## Работа

Положите запись в `<видео>/fragments/F1/F1.wav` и запустите в Claude Code:

```text
/ae-video:make <папка видео> F1
```

Плагин скажет, что сделано и что нужно от вас. Работу можно прервать в любой момент: состояние хранится в `state.json`, и команда продолжит с того же места.

## Документы

- [CLAUDE.md](CLAUDE.md): принципы, пайплайн, карта репозитория.
- [PLAN.md](PLAN.md): план развития.
- [docs/base.md](docs/base.md): устройство базы.
- [docs/settings.md](docs/settings.md): настройки.
- [docs/formats.md](docs/formats.md): форматы файлов.

## Лицензии

Часть правил движения в основах монтажа пересказана и адаптирована для видео по материалам `emilkowalski/skills` (MIT).

Навык `plugin/skills/after-effects` взят из `aedev-tools/adobe-agent-skills`, Apache-2.0, текст лицензии лежит рядом с ним.

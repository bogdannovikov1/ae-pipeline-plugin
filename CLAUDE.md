# Audio to Transcript Agent

Первый этап конвейера анимации для After Effects. Общаемся на русском.

1. **Транскрипт (этот проект):** аудио → `<аудио>.timeline.json`. Скрипт `transcribe.py`, агент `transcriber`.
2. **План анимации:** агент спрашивает, пользователь отвечает голосом или текстом, результат — записи с привязкой к номерам слов: `{"at_word": 42, "until_word": 57, "animation": "chart_grow", "params": {}}`.
3. **JSX:** постоянный отлаженный скрипт ExtendScript читает JSON и строит слои из библиотеки проверенных анимаций. Каждый раз генерировать весь скрипт с нуля не нужно.

`refine_timings.py` подрезает границы слов по громкости, исходные таймкоды Whisper лежат в `raw_start` и `raw_end`. `transcribe.py` запускает его сам, готовый JSON можно обработать отдельно.

Маркеры по транскрипту ставит `ae/add_word_markers.jsx`, правки с маркеров обратно в JSON переносит `ae/update_transcript_from_markers.jsx`. Ревью делается до плана анимации: добавление и удаление слов меняет `i`. Слова с `manual: true` подрезка не трогает. После ревью `approve_transcript.py` замораживает транскрипт: ставит `status: approved` и `transcript_hash`, по которому следующие этапы проверяют актуальность. Навык `.claude/skills/after-effects` скопирован из `aedev-tools/adobe-agent-skills` (Apache-2.0).

Движок `ae/engine/build_scene.jsx` строит композицию из `scene.json`, блоки берёт из базы. Время задаётся через `at_word`, `after` или `at`, плюс необязательный `delay`.

Базы лежат в `~/Documents/Projects/Videos/bases/`. Ассеты и блоки меняются только через агента `librarian`.

Поле `i` в `words` — контракт между этапами: его нельзя менять без пересчёта ссылок. При изменении формата JSON увеличивай `schema_version`.

Железо: MacBook Air M4, 16 ГБ. Распознавание идёт локально через `mlx-whisper`, нужен `ffmpeg`.

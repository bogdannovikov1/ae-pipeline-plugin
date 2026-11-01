# Форматы

Все файлы одного фрагмента лежат в `<видео>/fragments/<f>/`.

```text
<f>.wav                 запись
state.json              состояние этапов
<f>.timeline.json       транскрипт
<f>.notes.md            заметки по транскрипту
<f>.animation.json      план анимации
<f>.scene.json          сцена для движка
blocks/<id>/            блоки этого видео, главнее блоков базы
<f>.sounds.json         журнал звуков последней сборки
reviews/round_N.json    отчёты предревью
reviews/manual_N.json   отчёты разового ревью
frames/round_N/         кадры, листы и index.json
<f>.aep                 проект фрагмента: ревью маркеров, сборка, доводка
<f>.base_proposals.md   предложения для базы
<f>.*.extended.json     копии до отмены расширения
```

## timeline.json

`schema_version: 4`. Поля: `status` (`draft` или `approved`), `source`, `language`, `model`, `fps`, `text`, `words`, `phrases`, после заморозки `transcript_hash` и `approved_at`.

Слово: `i`, `word`, `start`, `end`, `start_frame`, `end_frame`, `confidence`, `needs_review`, `raw_start`, `raw_end`, необязательное `manual`. Номера `i` идут подряд с нуля, на них ссылаются все следующие этапы.

После расширения появляется `parts`: `[{ "audio": "F1.wav", "at": 0, "first_word": 0 }, { "audio": "F1_p2.wav", "at": 27.9, "first_word": 79 }]`. Номера слов и время новой части продолжают старые. Часть живёт в `F1_p2.wav` и `F1_p2.timeline.json`, а `<f>.timeline.json` содержит всё.

## animation.json

```json
{
  "transcript_hash": "...",
  "beats": [
    { "at_word": 3, "until_word": 4, "intent": "Появляется массив", "block": "array_cells", "params": { "count": 5 } }
  ]
}
```

Пустой `block` значит, что автор выбрал новый блок, а `intent` становится заданием для `animator`.

## scene.json

```json
{
  "base": "starter",
  "audio": "F1.wav",
  "timeline": "F1.timeline.json",
  "transcript_hash": "...",
  "tail": 0.8,
  "comp": { "name": "F1", "width": 1920, "height": 1080 },
  "tokens": {},
  "items": [
    { "id": "cells", "block": "array_cells", "at_word": 3, "until_word": 9, "params": {}, "tokens": {} }
  ]
}
```

Пути относительно папки сцены или абсолютные, база задаётся именем или путём. Время элемента: `at_word`, `after` (id элемента) или `at` (секунды), плюс необязательный `delay`. `always: true` ставит элемент на всю длину. `anchor_reason` объясняет, почему элемент намеренно стоит в начале длинной фразы: проверка тайминга его не трогает.

## sounds.json

Журнал звуков последней сборки: `{ "sounds": [{ "item", "sound", "event", "start", "end", "animation_start", "animation_end" }] }`. Его проверяет `view.py sounds`.

## state.json

`version` (2), `fragment`, `audio`, `parts`, `rounds`, `stages`. Запись в `parts`: `audio`, `keep` (что расширение не должно изменить), `append` (была ли ручная доводка), `stages_before` (статусы до расширения, для отмены). Запись этапа: `status`, `note`, `inputs`, `outputs` (хэши файлов), `settings` (хэш настроек), `finished_at`, у пропущенного этапа `skipped: true`.

Статусы: `pending`, `running`, `review`, `approved`, `rejected`, `stale`. Файл меняет только `pipeline.py`.

## reviews/round_N.json

```json
{
  "summary": "...",
  "not_checked": ["звук"],
  "findings": [
    { "rule": "color.contrast", "time": 12.4, "severity": "major", "what": "...", "fix": "..." }
  ]
}
```

Находки считаются одной и той же, если совпали `rule` и `time`.

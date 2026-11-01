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
reviews/round_N.json    отчёты предревью
frames/round_N/         кадры, листы и index.json
<f>.aep                 проект после доводки
<f>.base_proposals.md   предложения для базы
```

## timeline.json

`schema_version: 4`. Поля: `status` (`draft` или `approved`), `source`, `language`, `model`, `fps`, `text`, `words`, `phrases`, после заморозки `transcript_hash` и `approved_at`.

Слово: `i`, `word`, `start`, `end`, `start_frame`, `end_frame`, `confidence`, `needs_review`, `raw_start`, `raw_end`, необязательное `manual`. Номера `i` идут подряд с нуля, на них ссылаются все следующие этапы.

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
  "base": "base_algoritms",
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

Пути относительно папки сцены или абсолютные, база задаётся именем или путём. Время элемента: `at_word`, `after` (id элемента) или `at` (секунды), плюс необязательный `delay`. `always: true` ставит элемент на всю длину.

## state.json

`version`, `fragment`, `audio`, `rounds`, `stages`. Запись этапа: `status`, `note`, `inputs`, `outputs` (хэши файлов), `settings` (хэш настроек), `finished_at`.

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

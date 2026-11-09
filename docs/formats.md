# Форматы

Все файлы одного фрагмента лежат в `<видео>/fragments/<f>/`.

```text
<f>.wav                 запись: готовая или озвученная из текста
<f>.script.txt          текст автора как есть (только для озвучки)
<f>.voice.txt           тот же текст с тегами интонации, вход ElevenLabs
<f>.voice_raw.wav       озвучка без EQ
<f>.take<N>.wav         прошлые дубли озвучки
state.json              состояние этапов
<f>.timeline.json       транскрипт
<f>.notes.md            заметки по транскрипту
<f>.animation.json      план анимации
<f>.scene.json          сцена для движка
blocks/<id>/            блоки этого видео, главнее блоков базы
<f>.sounds.json         журнал сборки: звуки и начала элементов
<f>.layout.json         положение слоёв из AE, большой файл только для view.py
<f>.build.json          отметка успешной сборки с хэшем сцены
reviews/<f>.check.json  отчёт ревью кода
reviews/round_N.json    отчёты предревью
reviews/manual_N.json   отчёты разового ревью
frames/round_N/         кадры, листы и index.json
<f>.aep                 проект фрагмента: ревью маркеров, сборка, доводка
<f>.base_proposals.md   предложения для базы
<f>.*.extended.json     копии до отмены расширения
```

## voice.txt

Текст автора без изменений слов, по фразе на строку. В начале строки — теги в квадратных скобках через запятую, только из `voice.allowed_tags`: `[slightly faster, thoughtful] Текст... дальше.` Проверка: `voice.py check`, она же идёт при `done voice`.

## timeline.json

`schema_version: 4`. Поля: `status` (`draft` или `approved`), `source`, `language`, `model`, `fps`, `text`, `words`, `phrases`, после заморозки `transcript_hash` и `approved_at`.

Слово: `i`, `word`, `start`, `end`, `start_frame`, `end_frame`, `confidence`, `needs_review`, `raw_start`, `raw_end`, необязательное `manual`. Номера `i` идут подряд с нуля, на них ссылаются все следующие этапы.

После расширения появляется `parts`: `[{ "audio": "F1.wav", "at": 0, "first_word": 0 }, { "audio": "F1_p2.wav", "at": 27.9, "first_word": 79 }]`. Номера слов и время новой части продолжают старые. Часть живёт в `F1_p2.wav` и `F1_p2.timeline.json`, а `<f>.timeline.json` содержит всё.

## animation.json

```json
{
  "transcript_hash": "...",
  "beats": [
    { "id": "intro", "at_word": 3, "until_word": 4, "intent": "Появляется массив", "block": "array_cells", "blocks": ["array_cells"], "params": { "count": 5 } }
  ]
}
```

План — единственная постановка: уточнения автора из чата записываются в `intent` и `params` бита до сборки.

- `id` — уникальное имя бита, на него ссылаются элементы сцены. Бит без `id` получает `b1`, `b2`… по порядку: старые планы работают.
- `blocks` — все блоки, утверждённые автором для бита. Без него утверждённым считается `block`.
- Пустой `block` и нет `blocks` — автор выбрал новый блок, а `intent` становится заданием для `animator`.
- Необязательные поля режиссёра (`director`): `story` у плана — главная мысль и структура; у бита `uses` — id битов, чьи объекты он продолжает, `layout` — зоны кадра (`center`, `left`, `right`, `top`, `bottom`), `captions` — для `subtitles`: `accents`, номера или тексты ключевых слов.

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
    { "id": "cells", "beat": "intro", "block": "array_cells", "at_word": 3, "until_word": 9, "params": {}, "tokens": {} }
  ]
}
```

Пути относительно папки сцены или абсолютные, база задаётся именем или путём. Время элемента: `at_word`, `after` (id элемента) или `at` (секунды), плюс необязательный `delay`. `always: true` ставит элемент на всю длину. `anchor_reason` объясняет, почему элемент намеренно стоит в начале длинной фразы: проверка тайминга его не трогает.

`beat` — `id` бита плана, `"global"` для фона и общего, или список id, если объект живёт через несколько битов (таблица, которую следующий бит заполняет): такой элемент засчитывается каждому из них. `plan_check.py` (он же внутри `validate.py scene` и `view.py check`) сверяет сцену с `<f>.animation.json`:

- ошибка: у бита нет утверждённого главного блока, элемент ссылается на неизвестный бит, бит с блоками не собран, `target` указывает на несуществующий или более поздний элемент;
- предупреждение: не использован вспомогательный блок из `plan_check.helper_blocks`, блок не указан в плане бита, элемент без `beat`, `target` на блок не того типа, который назван в описании параметра `target` блока («id блока <имя>»).

Блоки общих элементов (`beat: "global"` или `always` без `beat`), например фон, засчитываются каждому биту. Сцена, где ни у одного элемента нет `beat`, — старый формат: сверка с планом пропускается с одним предупреждением.

## sounds.json

Журнал сборки: `{ "sounds": [{ "item", "sound", "event", "start", "end", "animation_start", "animation_end" }], "starts": { "<id элемента>": секунды } }`. Звуки проверяет `view.py sounds`, по `starts` снимает кадры `capture.py`.

## state.json

`version` (2), `fragment`, `audio`, `parts`, `rounds`, `stages`. Запись в `parts`: `audio`, `keep` (что расширение не должно изменить), `append` (была ли ручная доводка), `stages_before` (статусы до расширения, для отмены). Запись этапа: `status`, `note`, `inputs`, `outputs` (хэши файлов), `settings` (хэш настроек), `finished_at`, у пропущенного этапа `skipped: true`.

Статусы: `pending`, `running`, `review`, `approved`, `rejected`, `stale`. Файл меняет только `pipeline.py`.

## reviews/<f>.check.json

Отчёт ревью кода (`checker`), один на сборку. Проверяет `validate.py check`.

```json
{
  "summary": "...",
  "findings": [
    { "beat": "b4", "item": "ptr_mid", "severity": "critical", "what": "...", "fix": "..." }
  ]
}
```

В находке обязательны `severity` (`minor`, `major`, `critical`), `beat` или `item`, `what` и `fix`. Находки не ниже `code_review.block_on` аниматор правит один раз, затем `pipeline.py finish check`.

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

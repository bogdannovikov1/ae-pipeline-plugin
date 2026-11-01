# Настройки

Слои, каждый следующий перекрывает предыдущий по ключам:

1. `plugin/config/defaults.json`: значения по умолчанию.
2. `~/.config/ae-video/settings.json`: личные, переживают обновления плагина.
3. `<видео>/settings.json`: одно видео.

Посмотреть итог:

```bash
python3 plugin/scripts/settings.py show --video <папка видео>
```

Стиль видео (цвета, движение, `lead`, пружина) в настройки не входит: он лежит в `tokens.json` базы. Менять его локально можно полем `tokens` в сцене.

| Ключ | Значение |
|---|---|
| `bases_dir` | Папка с базами |
| `base` | Активная база |
| `transcribe.model` | Модель MLX Whisper |
| `transcribe.language` | Язык записи |
| `transcribe.review_below` | Слова с уверенностью ниже помечаются на проверку |
| `refine.frame_seconds` | Длина кадра анализа громкости, с |
| `refine.min_voiced_frames` | Меньшая серия кадров считается щелчком, а не речью |
| `refine.max_gap_frames` | Более длинная тишина разделяет слово на куски |
| `refine.max_below_speech_db` | Что настолько тише речи, речью не считается |
| `refine.pad_start`, `refine.pad_end` | Запас вокруг слова, с |
| `refine.threshold_position` | Порог между шумом (0) и речью (1) |
| `ae.app` | Имя приложения After Effects |
| `ae.timeout` | Предел выполнения скрипта в AE, с |
| `ae.launch_timeout` | Ожидание запуска After Effects, с |
| `ae.review_width`, `ae.review_height`, `ae.review_fps` | Композиция ревью; fps берётся из транскрипта, если он там есть |
| `ae.autosave_projects` | Папки, где проект можно сохранять без вопроса |
| `review.max_rounds` | Максимум кругов предревью |
| `review.block_on` | С какой серьёзности находка предлагается к исправлению: `minor`, `major`, `critical`; `none` только отчёт |
| `review.max_frames` | Лимит кадров на круг |
| `review.offsets` | Сдвиги кадров от начала элемента, с |
| `review.frame_timeout` | Ожидание сохранения кадров, с |
| `review.sheet_columns`, `review.sheet_rows` | Сетка контактного листа |
| `review.sheet_frame_width` | Ширина кадра на листе, px |
| `base_update.every_fragments` | Этап `curate` на каждом N-м фрагменте; 0 отключает |
| `base_update.min_repeats` | Со скольких повторов факт попадает в предложения |
| `extend.gap_seconds` | Пауза между частями расширенного фрагмента, с |
| `timing.max_early` | Насколько раньше слова может стартовать анимация, с |
| `timing.long_phrase_words`, `timing.long_phrase_seconds` | С какой длины фраза считается длинной |
| `timing.long_phrase_zone` | Доля фразы, после которой начинается зона опоры: 0,5 значит вторая половина |

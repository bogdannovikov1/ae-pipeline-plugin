---
name: setup
description: Первая настройка плагина: проверяет компьютер и создаёт рабочую базу. Отдельная команда автора.
disable-model-invocation: true
---

1. Запусти `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/setup_base.py`. Существующую базу команда не трогает.
2. Запусти `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py`.
3. Покажи только то, чего не хватает, и что сделать по каждому пункту. Сам ничего не устанавливай: спроси автора перед установкой.
4. Напомни, что в After Effects нужно один раз включить Settings > Scripting & Expressions > Allow Scripts to Write Files and Access Network.
5. Скажи, где лежит база и что `style.md` и `craft.md` в ней — шаблоны для заполнения, а следующий шаг — `/ae-video:make`.

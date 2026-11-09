#!/usr/bin/env python3
"""Сравнивает правленую автором композицию со снимком последней сборки и переносит однозначное в сцену.

Снимок `<f>.snapshot.json` пишет `ae_run.py build`, выгрузку правленой композиции — `ae_run.py diff`.
Сюда они приходят файлами, After Effects не нужен.

Пример:
    python3 scene_diff.py F1.scene.json edited.json
    python3 scene_diff.py F1.scene.json edited.json --apply
"""
import argparse
import json
import re
import sys
from pathlib import Path

from settings import settings_for
from validate import read_json

APPLY_KEYS = ("offset", "scale", "rotation")
ORDER_KEYS = ("below", "above")


def snapshot_path(scene):
    scene = Path(scene)
    return scene.with_name(re.sub(r"(\.scene)?\.json$", ".snapshot.json", scene.name))


def item_id(item, number):
    return item.get("id") or f"{item.get('block')}_{number}"


# ---- Слои по элементам

def by_item(data):
    """{элемент: {ключ слоя: слой}} для видеослоёв сцены. Ключ — имя и номер среди одноимённых сверху вниз."""
    result = {}
    keys = {}
    for layer in sorted(data.get("layers", []), key=lambda one: one["index"]):
        if not layer.get("item") or not layer.get("video", True):
            continue
        group = result.setdefault(layer["item"], {})
        key, number = layer["name"], 1
        while key in group:
            number += 1
            key = f"{layer['name']}#{number}"
        group[key] = layer
        keys[layer["index"]] = (layer["item"], key)
    return result, keys


def parent_of(layer, keys, data):
    """Родитель слоя так, чтобы его можно было сравнить между композициями: элемент и ключ или имя."""
    index = layer.get("parent") or 0
    if not index:
        return None
    if index in keys:
        return keys[index]
    for other in data.get("layers", []):
        if other["index"] == index:
            return ("", other["name"])
    return ("", str(index))


# ---- Разница свойств

def values_of(prop):
    """Список (время, значение) свойства: одно значение без времени или ключи."""
    if prop is None:
        return None
    if "keys" in prop:
        return [(time, value) for time, value in prop["keys"]]
    return [(None, prop.get("v"))]


def vector(value):
    return list(value) if isinstance(value, list) else [value]


def same_times(before, after, eps):
    if len(before) != len(after):
        return False
    return all((a is None and b is None) or (a is not None and b is not None and abs(a - b) <= eps)
               for (a, _), (b, _) in zip(before, after))


def offset_delta(before, after, tol):
    """Сдвиг между значениями свойства: список, None — без изменений, строка — почему неоднозначно."""
    old, new = values_of(before), values_of(after)
    if old is None or new is None:
        return None
    if not same_times(old, new, tol["seconds"]):
        return "ключи изменены"
    deltas = []
    for (_, a), (_, b) in zip(old, new):
        a, b = vector(a), vector(b)
        size = max(len(a), len(b))
        a, b = a + [0] * (size - len(a)), b + [0] * (size - len(b))
        deltas.append([y - x for x, y in zip(a, b)])
    first = deltas[0]
    if any(any(abs(x - y) > tol["px"] for x, y in zip(first, other)) for other in deltas[1:]):
        return "ключи сдвинуты по-разному"
    if all(abs(x) <= tol["px"] for x in first):
        return None
    return first


def position_delta(before, after, tol):
    """Сдвиг позиции, в том числе раздельной по осям."""
    if before is None or after is None:
        return None
    if ("dims" in before) != ("dims" in after):
        return "позицию разделили или объединили по осям"
    if "dims" not in before:
        return offset_delta(before, after, tol)
    result = []
    for old, new in zip(before["dims"], after["dims"]):
        one = offset_delta(old, new, tol)
        if isinstance(one, str):
            return one
        result.append(one[0] if one else 0)
    return result if any(abs(x) > tol["px"] for x in result) else None


def scale_ratio(before, after, tol):
    """Равномерный множитель масштаба: число, None — без изменений, строка — почему неоднозначно."""
    old, new = values_of(before), values_of(after)
    if old is None or new is None:
        return None
    if not same_times(old, new, tol["seconds"]):
        return "ключи масштаба изменены"
    ratios = []
    for (_, a), (_, b) in zip(old, new):
        for x, y in list(zip(vector(a), vector(b)))[:2]:
            if abs(x) < 1e-6:
                if abs(y) > 1e-6:
                    return "масштаб из нуля"
                continue
            ratios.append(y / x)
    if not ratios:
        return None
    if any(abs(r - ratios[0]) > tol["scale"] for r in ratios):
        return "неравномерный масштаб"
    return None if abs(ratios[0] - 1) <= tol["scale"] else ratios[0]


def rotation_delta(before, after, tol):
    one = offset_delta(before, after, dict(tol, px=tol["degrees"]))
    return one[0] if isinstance(one, list) else one


def changed(before, after, tol):
    """Изменилось ли свойство вообще, без разбора как."""
    old, new = values_of(before), values_of(after)
    if old is None or new is None:
        return False
    if not same_times(old, new, tol["seconds"]):
        return True
    return any(any(abs(x - y) > tol["px"] for x, y in zip(vector(a), vector(b))) or len(vector(a)) != len(vector(b))
               for (_, a), (_, b) in zip(old, new))


def layer_change(name, old, new, tol, parents):
    """Правки одного слоя: {offset, scale, rotation} и список неоднозначных пометок."""
    found, notes = {}, []
    props_old, props_new = old.get("props", {}), new.get("props", {})
    for key, fn in (("offset", lambda: position_delta(props_old.get("position"), props_new.get("position"), tol)),
                    ("scale", lambda: scale_ratio(props_old.get("scale"), props_new.get("scale"), tol)),
                    ("rotation", lambda: rotation_delta(props_old.get("rotation"), props_new.get("rotation"), tol))):
        value = fn()
        if isinstance(value, str):
            notes.append(f"{name}: {value}")
        elif value is not None:
            found[key] = value
    if changed(props_old.get("opacity"), props_new.get("opacity"), tol):
        notes.append(f"{name}: непрозрачность")
    if changed(props_old.get("anchor"), props_new.get("anchor"), tol):
        notes.append(f"{name}: опорная точка")
    if old.get("inner") != new.get("inner"):
        notes.append(f"{name}: содержимое (фигура, текст или эффекты)")
    if any(abs((old.get(k) or 0) - (new.get(k) or 0)) > tol["seconds"] for k in ("in", "out", "start")):
        notes.append(f"{name}: время слоя")
    if old.get("enabled", True) != new.get("enabled", True):
        notes.append(f"{name}: {'выключен' if not new.get('enabled', True) else 'включён'}")
    if parents[0] != parents[1]:
        notes.append(f"{name}: родитель")
    return found, notes


def same_change(a, b, tol):
    for key in APPLY_KEYS:
        x, y = a.get(key), b.get(key)
        if (x is None) != (y is None):
            return False
        if key == "offset" and x is not None:
            size = max(len(x), len(y))
            x, y = x + [0] * (size - len(x)), y + [0] * (size - len(y))
            if any(abs(p - q) > tol["px"] for p, q in zip(x, y)):
                return False
        elif x is not None and abs(x - y) > (tol["scale"] if key == "scale" else tol["degrees"]):
            return False
    return True


def item_changes(old_layers, new_layers, keys_old, keys_new, old_data, new_data, tol):
    """Правка элемента целиком (одинаковая на всех корневых слоях) и неоднозначные пометки."""
    notes = []
    gone = [key for key in old_layers if key not in new_layers]
    added = [key for key in new_layers if key not in old_layers]
    if gone:
        notes.append("удалены слои: " + ", ".join(gone))
    if added:
        notes.append("новые слои: " + ", ".join(added))
    roots, inside = {}, []
    for key, old in old_layers.items():
        if key not in new_layers:
            continue
        new = new_layers[key]
        parents = (parent_of(old, keys_old, old_data), parent_of(new, keys_new, new_data))
        found, layer_notes = layer_change(key, old, new, tol, parents)
        notes += layer_notes
        own_parent = parents[0] and parents[0][0] == old.get("item")
        if own_parent:
            if found:
                inside.append(key)
        else:
            roots[key] = found
    if inside:
        notes.append("правка внутри элемента: " + ", ".join(inside))
    moved = {key: one for key, one in roots.items() if one}
    if not moved:
        return {}, notes
    values = list(roots.values())
    if all(same_change(values[0], other, tol) for other in values[1:]):
        return values[0], notes
    notes.append("слои элемента изменены по-разному: " + ", ".join(
        f"{key} {describe(one)}" for key, one in moved.items()))
    return {}, notes


# ---- Порядок слоёв

def spans(layers_by_item, items):
    """Где элемент стоит в стопке: (верх, низ, середина) по индексам слоёв."""
    result = {}
    for ident in items:
        indices = [layer["index"] for layer in layers_by_item.get(ident, {}).values()]
        if indices:
            result[ident] = (min(indices), max(indices), sum(indices) / len(indices))
    return result


def move(order, ident, mode, partner):
    """Порядок сверху вниз после переноса элемента под (below) или над (above) другим, как делает движок."""
    if ident not in order or partner not in order:
        return order
    order = [one for one in order if one != ident]
    place = order.index(partner)
    order.insert(place + 1 if mode == "below" else place, ident)
    return order


def expected_order(old_spans, scene_items, snapshot_adjust):
    """Порядок, который даст сцена сейчас: снимок плюс переносы, добавленные в сцену после сборки."""
    order = sorted(old_spans, key=lambda ident: old_spans[ident][2])
    for number, item in enumerate(scene_items):
        ident = item_id(item, number)
        adjust = item.get("adjust") or {}
        before = snapshot_adjust.get(ident) or {}
        for mode in ORDER_KEYS:
            if adjust.get(mode) and adjust.get(mode) != before.get(mode):
                order = move(order, ident, mode, adjust[mode])
    return order


def order_changes(order, new_spans, scene_index):
    """Переносы below/above, которые дают порядок автора. Возвращает ({id: (режим, партнёр)}, пометки)."""
    common = [ident for ident in order if ident in new_spans]
    rank = {ident: n for n, ident in enumerate(common)}
    flips, notes = [], []
    for a in common:
        for b in common:
            if rank[a] >= rank[b]:
                continue
            top_a, bottom_a, _ = new_spans[a]
            top_b, bottom_b, _ = new_spans[b]
            if bottom_b < top_a:
                flips.append((a, b))
            elif not bottom_a < top_b:
                notes.append(f"порядок: слои {a} и {b} перемешаны")
    if not flips:
        return {}, notes
    count = {}
    for a, b in flips:
        count[a] = count.get(a, 0) + 1
        count[b] = count.get(b, 0) + 1
    moves, left = {}, set(flips)
    for ident in sorted(count, key=lambda one: (-count[one], scene_index.get(one, 0))):
        partners = [b if a == ident else a for a, b in left if ident in (a, b)]
        if not partners:
            continue
        below = [p for p in partners if new_spans[ident][0] > new_spans[p][1]]
        if len(below) == len(partners):
            moves[ident] = ("below", max(partners, key=lambda p: new_spans[p][1]))
        elif not below:
            moves[ident] = ("above", min(partners, key=lambda p: new_spans[p][0]))
        else:
            continue
        left = {pair for pair in left if ident not in pair}
    result = list(order)
    for ident in sorted(moves, key=lambda one: scene_index.get(one, 0)):
        result = move(result, ident, *moves[ident])
    wanted = sorted(common, key=lambda ident: new_spans[ident][2])
    got = [ident for ident in result if ident in new_spans]
    if left or got != wanted:
        return {}, notes + ["порядок слоёв изменён, но below/above его не выражают: " + " > ".join(wanted)]
    return moves, notes


# ---- Итог

def number(value, digits=1):
    text = f"{value:+.{digits}f}"
    text = text.rstrip("0").rstrip(".") if "." in text else text
    return "0" if text in ("+0", "-0") else text


def describe(change):
    parts = []
    if change.get("offset"):
        offset = change["offset"][:2] if len(change["offset"]) > 2 and not change["offset"][2] else change["offset"]
        parts.append("сдвиг (" + ", ".join(number(x) for x in offset) + ")")
    if change.get("scale"):
        parts.append(f"масштаб ×{change['scale']:.3f}".rstrip("0").rstrip("."))
    if change.get("rotation"):
        parts.append(f"поворот {number(change['rotation'], 2)}°")
    return ", ".join(parts)


def diff(scene, snapshot, edited, settings):
    """Сравнение: {элемент: {change, order, notes}} и пометки без элемента."""
    tol = settings.get("diff", {})
    tol = {"px": tol.get("px", 0.5), "scale": tol.get("scale", 0.002), "degrees": tol.get("degrees", 0.05),
           "seconds": tol.get("seconds", 0.002)}
    old_items, keys_old = by_item(snapshot)
    new_items, keys_new = by_item(edited)
    items = scene.get("items", [])
    scene_index = {item_id(item, n): n for n, item in enumerate(items)}
    snapshot_adjust = snapshot.get("adjust", {})
    result = {}
    loose = []
    for ident in scene_index:
        if ident not in old_items:
            continue
        if ident not in new_items:
            result[ident] = {"change": {}, "order": None, "notes": ["слоёв элемента нет в композиции"]}
            continue
        change, notes = item_changes(old_items[ident], new_items[ident], keys_old, keys_new, snapshot, edited, tol)
        change = remaining(change, items[scene_index[ident]].get("adjust") or {}, snapshot_adjust.get(ident) or {}, tol)
        result[ident] = {"change": change, "order": None, "notes": notes}
    unknown = sorted(set(new_items) - set(scene_index))
    if unknown:
        loose.append("слои элементов, которых нет в сцене: " + ", ".join(unknown))
    known = {one["name"] for one in snapshot.get("layers", []) if not one.get("item")}
    untagged = [layer["name"] for layer in edited.get("layers", [])
                if layer.get("video", True) and not layer.get("item") and layer["name"] not in known]
    if untagged:
        loose.append("новые слои без элемента: " + ", ".join(untagged))
    order = expected_order(spans(old_items, scene_index), items, snapshot_adjust)
    moves, order_notes = order_changes(order, spans(new_items, scene_index), scene_index)
    loose += order_notes
    for ident, one in moves.items():
        result[ident]["order"] = one
    return result, loose


def remaining(change, adjust, built, tol):
    """Часть правки, которой ещё нет в сцене: --apply уже мог перенести её после сборки."""
    if not change:
        return change
    result = dict(change)
    done_offset = [x - y for x, y in zip(pad(adjust.get("offset"), 3), pad(built.get("offset"), 3))]
    if result.get("offset") is not None:
        rest = [x - y for x, y in zip(pad(result["offset"], 3), done_offset)]
        size = len(result["offset"])
        result["offset"] = rest[:size] if any(abs(x) > tol["px"] for x in rest) else None
    if result.get("scale") is not None:
        k = result["scale"] / ((adjust.get("scale") or 1) / (built.get("scale") or 1))
        result["scale"] = k if abs(k - 1) > tol["scale"] else None
    if result.get("rotation") is not None:
        turn = result["rotation"] - ((adjust.get("rotation") or 0) - (built.get("rotation") or 0))
        result["rotation"] = turn if abs(turn) > tol["degrees"] else None
    return {key: value for key, value in result.items() if value is not None}


def pad(value, size):
    value = list(value or [])
    return value + [0] * (size - len(value))


def summary(result, loose, settings, applied=False):
    """Короткая сводка для модели: одна строка на элемент."""
    limit = settings.get("diff", {}).get("max_lines", 10)
    lines, ready, unclear = [], 0, 0
    for ident, one in result.items():
        parts = []
        if one["change"]:
            parts.append(describe(one["change"]))
        if one["order"]:
            mode, partner = one["order"]
            parts.append(f"{'под' if mode == 'below' else 'над'} {partner}")
        if parts:
            ready += 1
        if one["notes"]:
            unclear += 1
            parts.append("проверить: " + "; ".join(one["notes"]))
        if parts:
            lines.append(f"{ident}: " + "; ".join(parts))
    lines += ["проверить: " + note for note in loose]
    if not lines:
        return "Правок автора нет: композиция совпадает со сценой"
    head = (f"{'Перенесено' if applied else 'Можно перенести (--apply)'}: {ready} эл.; "
            f"неоднозначно: {unclear + len(loose)}")
    if len(lines) > limit:
        lines = lines[:limit - 1] + [f"… ещё {len(lines) - limit + 1} строк, полный список: --all"]
    return "\n".join([head] + lines)


def apply(scene, result):
    """Переносит однозначное в adjust элементов. Возвращает число изменённых элементов."""
    count = 0
    for number, item in enumerate(scene.get("items", [])):
        one = result.get(item_id(item, number))
        if not one or not (one["change"] or one["order"]):
            continue
        adjust = dict(item.get("adjust") or {})
        change = one["change"]
        if change.get("offset"):
            size = max(len(change["offset"]), len(adjust.get("offset") or []))
            adjust["offset"] = [round(x + y, 1) for x, y in zip(pad(adjust.get("offset"), size), pad(change["offset"], size))]
        if change.get("scale"):
            adjust["scale"] = round((adjust.get("scale") or 1) * change["scale"], 4)
        if change.get("rotation"):
            adjust["rotation"] = round((adjust.get("rotation") or 0) + change["rotation"], 2)
        if one["order"]:
            mode, partner = one["order"]
            for key in ORDER_KEYS:
                adjust.pop(key, None)
            adjust[mode] = partner
        if adjust.get("offset") and not any(adjust["offset"]):
            adjust.pop("offset")
        if adjust.get("scale") == 1:
            adjust.pop("scale")
        if adjust.get("rotation") == 0:
            adjust.pop("rotation")
        if adjust:
            item["adjust"] = adjust
        else:
            item.pop("adjust", None)
        count += 1
    return count


def run(scene_path, edited, settings, write=False, show_all=False):
    """Сравнивает и, если write, пишет сцену. Возвращает текст сводки."""
    scene_path = Path(scene_path)
    snap = snapshot_path(scene_path)
    if not snap.is_file():
        raise FileNotFoundError(f"нет снимка сборки {snap.name}: пересоберите сцену через ae_run.py build")
    scene = read_json(scene_path)
    result, loose = diff(scene, read_json(snap), edited, settings)
    if show_all:
        settings = dict(settings, diff=dict(settings.get("diff", {}), max_lines=10 ** 6))
    if write and apply(scene, result):
        scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary(result, loose, settings, applied=write)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scene", type=Path)
    parser.add_argument("edited", type=Path, help="выгрузка правленой композиции (export_transforms.jsx)")
    parser.add_argument("--apply", action="store_true", help="перенести однозначные правки в сцену")
    parser.add_argument("--all", action="store_true", help="все строки сводки")
    args = parser.parse_args()
    try:
        print(run(args.scene, read_json(args.edited), settings_for(args.scene), args.apply, args.all))
    except FileNotFoundError as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()

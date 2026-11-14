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
        return [(key[0], key[1]) for key in prop["keys"]]
    return [(None, prop.get("v"))]


def vector(value):
    return list(value) if isinstance(value, list) else [value]


def same_times(before, after, eps):
    if len(before) != len(after):
        return False
    return all((a is None and b is None) or (a is not None and b is not None and abs(a - b) <= eps)
               for (a, _), (b, _) in zip(before, after))


def shifted(prop, dt):
    """Свойство с временем ключей, сдвинутым назад на dt: так ключи слоя, который двигали по времени, сравнимы."""
    if not prop or not dt:
        return prop
    result = dict(prop)
    if "keys" in prop:
        result["keys"] = [[key[0] - dt] + list(key[1:]) for key in prop["keys"]]
    if "dims" in prop:
        result["dims"] = [shifted(one, dt) for one in prop["dims"]]
    return result


def has_keys(layer):
    props = layer.get("props", {}).values()
    return any(prop and ("keys" in prop or any("keys" in one for one in prop.get("dims", []))) for prop in props)


def key_change(old, new, tol):
    """Что сделали с ключами по времени: сводка для модели."""
    count_old = sum(1 for time, _ in old if time is not None)
    count_new = sum(1 for time, _ in new if time is not None)
    if count_old != count_new:
        return f"ключей {count_old}→{count_new}"
    deltas = [b - a for (a, _), (b, _) in zip(old, new)]
    if all(abs(one - deltas[0]) <= tol["seconds"] for one in deltas):
        return f"ключи сдвинуты по времени на {number(deltas[0], 2)} с"
    return "время ключей изменено"


def rest_move(old, new, tol):
    """Слой с ключами перетащили в кадре после конца движения: AE поменял последний ключ или добавил новый.

    Тогда автор двигал место покоя, и это сдвиг всего элемента. Остальные ключи должны остаться как были."""
    keyed = [one for one in old if one[0] is not None]
    if not keyed or len(new) not in (len(old), len(old) + 1):
        return None
    head = new[:len(keyed) - 1]
    if not same_times(keyed[:-1], head, tol["seconds"]) or changed_values(keyed[:-1], head, tol):
        return None
    last_time, last_value = keyed[-1]
    if len(new) == len(old):
        time, value = new[-1]
        if time is None or abs(time - last_time) > tol["seconds"]:
            return None
    else:
        if changed_values([keyed[-1]], [new[-2]], tol) or abs(new[-2][0] - last_time) > tol["seconds"]:
            return None
        time, value = new[-1]
        if time is None or time < last_time:
            return None
    a, b = vector(last_value), vector(value)
    size = max(len(a), len(b))
    delta = [y - x for x, y in zip(a + [0] * (size - len(a)), b + [0] * (size - len(b)))]
    return delta if any(abs(x) > tol["px"] for x in delta) else None


def changed_values(old, new, tol):
    return any(len(vector(a)) != len(vector(b)) or any(abs(x - y) > tol["px"] for x, y in zip(vector(a), vector(b)))
               for (_, a), (_, b) in zip(old, new))


def offset_delta(before, after, tol, label="ключи"):
    """Сдвиг между значениями свойства: список, None — без изменений, строка — почему неоднозначно."""
    old, new = values_of(before), values_of(after)
    if old is None or new is None:
        return None
    if not same_times(old, new, tol["seconds"]):
        moved = rest_move(old, new, tol)
        return moved if moved else f"{label}: {key_change(old, new, tol)}"
    deltas = []
    for (_, a), (_, b) in zip(old, new):
        a, b = vector(a), vector(b)
        size = max(len(a), len(b))
        a, b = a + [0] * (size - len(a)), b + [0] * (size - len(b))
        deltas.append([y - x for x, y in zip(a, b)])
    first = deltas[0]
    if any(any(abs(x - y) > tol["px"] for x, y in zip(first, other)) for other in deltas[1:]):
        return rest_move(old, new, tol) or "ключи сдвинуты по-разному"
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
        return offset_delta(before, after, tol, "позиция")
    result = []
    for old, new in zip(before["dims"], after["dims"]):
        one = offset_delta(old, new, tol, "позиция")
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
        return f"масштаб: {key_change(old, new, tol)}"
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
    one = offset_delta(before, after, dict(tol, px=tol["degrees"]), "поворот")
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


PROP_NAMES = {"position": "позиция", "scale": "масштаб", "rotation": "поворот", "opacity": "непрозрачность",
              "anchor": "опорная точка"}
CHANGE_PROP = {"offset": "position", "scale": "scale", "rotation": "rotation"}


def curve_changed(before, after, tol):
    """Изменён ли изгиб ключей: интерполяция, ease или касательные пути. Время и значения сверяются отдельно."""
    old, new = (before or {}).get("keys"), (after or {}).get("keys")
    if not old or not new or len(old) != len(new):
        return False

    def flat(value):
        if isinstance(value, list):
            return [x for one in value for x in flat(one)]
        return [value] if isinstance(value, (int, float)) else []

    for a, b in zip(old, new):
        ca, cb = (a[2] if len(a) > 2 else None), (b[2] if len(b) > 2 else None)
        if not ca or not cb:
            continue
        if ca.get("i") != cb.get("i"):
            return True
        x, y = flat([ca.get("e"), ca.get("s")]), flat([cb.get("e"), cb.get("s")])
        if len(x) != len(y) or any(abs(p - q) > tol["px"] for p, q in zip(x, y)):
            return True
    return False


def layer_change(name, old, new, tol, parents):
    """Правки одного слоя: {offset, scale, rotation}, пометки, сдвиг по времени, свойства для замены целиком.

    Слой, который целиком передвинули по времени, сравнивается с ключами, сдвинутыми обратно.
    odd — {свойство: что с ним}: правка, которую не выразить сдвигом элемента; она переносится ключами слоя."""
    found, notes, odd = {}, [], {}
    dt = (new.get("start") or 0) - (old.get("start") or 0)
    dt = dt if abs(dt) > tol["seconds"] else 0
    props_old = old.get("props", {})
    props_new = {key: shifted(prop, dt) for key, prop in new.get("props", {}).items()}
    for key, fn in (("offset", lambda: position_delta(props_old.get("position"), props_new.get("position"), tol)),
                    ("scale", lambda: scale_ratio(props_old.get("scale"), props_new.get("scale"), tol)),
                    ("rotation", lambda: rotation_delta(props_old.get("rotation"), props_new.get("rotation"), tol))):
        prop = CHANGE_PROP[key]
        value = fn()
        if prop == "position" and isinstance(value, str) and "dims" in (props_new.get("position") or {}):
            notes.append(f"{name}: {value}")
        elif isinstance(value, str):
            odd[prop] = value
        elif curve_changed(props_old.get(prop), props_new.get(prop), tol):
            odd[prop] = f"{PROP_NAMES[prop]}: изгиб ключей"
        elif value is not None:
            found[key] = value
    for prop in ("opacity", "anchor"):
        before, after = props_old.get(prop), props_new.get(prop)
        if changed(before, after, tol):
            old_keys, new_keys = values_of(before), values_of(after)
            how = "" if same_times(old_keys, new_keys, tol["seconds"]) else ": " + key_change(old_keys, new_keys, tol)
            odd[prop] = PROP_NAMES[prop] + how
        elif curve_changed(before, after, tol):
            odd[prop] = f"{PROP_NAMES[prop]}: изгиб ключей"
    if old.get("inner") != new.get("inner"):
        notes.append(f"{name}: содержимое (фигура, текст или эффекты)")
    if any(abs((new.get(k) or 0) - (old.get(k) or 0) - dt) > tol["seconds"] for k in ("in", "out")):
        notes.append(f"{name}: обрезка слоя (вход или выход)")
    if old.get("enabled", True) != new.get("enabled", True):
        notes.append(f"{name}: {'выключен' if not new.get('enabled', True) else 'включён'}")
    if parents[0] != parents[1]:
        notes.append(f"{name}: родитель")
    return {"found": found, "notes": notes, "dt": dt, "odd": odd, "props": props_new}


def payload(prop, start):
    """Свойство слоя для adjust.layers: значение или ключи со временем от начала элемента и изгибом."""
    if "keys" in prop:
        return {"keys": [[round(key[0] - start, 3)] + list(key[1:]) for key in prop["keys"]]}
    return {"v": prop.get("v")}


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


def item_changes(old_layers, new_layers, keys_old, keys_new, old_data, new_data, tol, start=None):
    """Правка элемента целиком (одинаковая на всех корневых слоях), правки отдельных слоёв и пометки.

    Возвращает (change, notes, layers, roots): layers — {слой: {свойство: значение или ключи}} для adjust.layers,
    его нет без start (снимок старше начала элементов), тогда такие правки только печатаются."""
    notes = []
    gone = [key for key in old_layers if key not in new_layers]
    added = [key for key in new_layers if key not in old_layers]
    if gone:
        notes.append("удалены слои: " + ", ".join(gone))
    if added:
        notes.append("новые слои: " + ", ".join(added))
    roots, inside, times, edits = {}, {}, {}, {}
    for key, old in old_layers.items():
        if key not in new_layers:
            continue
        new = new_layers[key]
        parents = (parent_of(old, keys_old, old_data), parent_of(new, keys_new, new_data))
        one = layer_change(key, old, new, tol, parents)
        edits[key], times[key] = one, one["dt"]
        notes += one["notes"]
        if parents[0] and parents[0][0] == old.get("item"):
            if one["found"]:
                inside[key] = one["found"]
        else:
            roots[key] = one["found"]
    shift, shift_note = time_shift(times, roots, new_layers, tol)
    if shift_note:
        notes.append(shift_note)
    # Что пойдёт ключами слоя: правки, которые не выразить сдвигом, правки детей и корней, сдвинутых по-разному.
    own = {key: dict(one["odd"]) for key, one in edits.items() if one["odd"]}
    for key, found in inside.items():
        for name in found:
            own.setdefault(key, {})[CHANGE_PROP[name]] = describe({name: found[name]})
    moved = {key: one for key, one in roots.items() if one}
    change = {}
    if moved:
        values = list(roots.values())
        if all(same_change(values[0], other, tol) for other in values[1:]):
            change = dict(values[0])
        else:
            for key, found in moved.items():
                for name in found:
                    own.setdefault(key, {})[CHANGE_PROP[name]] = describe({name: found[name]})
    if shift:
        change["shift"] = shift
    layers = {}
    for key, props in own.items():
        if start is None:
            notes += [f"{key}: {why}" for why in props.values()]
            continue
        layers[key] = {prop: payload(edits[key]["props"][prop], start) for prop in props}
    return change, notes, layers, sorted(roots)


def time_shift(times, roots, new_layers, tol):
    """Сдвиг элемента по времени: одинаковый у корневых слоёв, у детей такой же или нулевой без ключей.

    Возвращает (секунды или 0, пометка или None)."""
    if not any(times.values()):
        return 0, None
    root_times = [times[key] for key in roots]
    first = root_times[0] if root_times else 0
    same = root_times and all(abs(one - first) <= tol["seconds"] for one in root_times)
    children = [key for key in times if key not in roots]
    follow = all(abs(times[key] - first) <= tol["seconds"] or (not times[key] and not has_keys(new_layers[key]))
                 for key in children)
    if same and first and follow:
        return first, None
    return 0, "слои сдвинуты по времени по-разному: " + ", ".join(
        f"{key} {number(dt, 2)} с" for key, dt in times.items() if dt)


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
    if change.get("shift"):
        parts.append(f"по времени {number(change['shift'], 2)} с")
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
        start = snapshot["starts"].get(ident) if isinstance(snapshot.get("starts"), dict) else None
        change, notes, layers, roots = item_changes(old_items[ident], new_items[ident], keys_old, keys_new,
                                                    snapshot, edited, tol, start)
        shift = change.pop("shift", 0)
        adjust = items[scene_index[ident]].get("adjust") or {}
        change = remaining(change, adjust, snapshot_adjust.get(ident) or {}, tol)
        layers = new_layer_edits(layers, adjust.get("layers") or {})
        result[ident] = {"change": change, "order": None, "notes": notes, "shift": shift, "delay": 0,
                         "layers": layers, "roots": roots}
    unknown = sorted(set(new_items) - set(scene_index))
    if unknown:
        loose.append("слои элементов, которых нет в сцене: " + ", ".join(unknown))
    known = {one["name"] for one in snapshot.get("layers", []) if not one.get("item")}
    untagged = [layer["name"] for layer in edited.get("layers", [])
                if layer.get("video", True) and not layer.get("item") and layer["name"] not in known]
    if untagged:
        loose.append("новые слои без элемента: " + ", ".join(untagged))
    delays(result, items, snapshot.get("delay"), tol)
    order = expected_order(spans(old_items, scene_index), items, snapshot_adjust)
    moves, order_notes = order_changes(order, spans(new_items, scene_index), scene_index)
    loose += order_notes
    for ident, one in moves.items():
        result[ident]["order"] = one
    return result, loose


def new_layer_edits(layers, stored):
    """Правки слоёв, которых ещё нет в сцене: повторный --apply их не пишет."""
    result = {}
    for key, props in layers.items():
        fresh = {prop: value for prop, value in props.items() if (stored.get(key) or {}).get(prop) != value}
        if fresh:
            result[key] = fresh
    return result


def delays(result, items, built, tol):
    """Сдвиг по времени в delay элементов: только ещё не перенесённая часть, без сдвига зависимых.

    Элемент с after стоит от начала цели: её delay сдвигает и его, поэтому его delay получает обратную поправку.
    Перенесённое после сборки (delay сцены против снимка) учитывается по всей цепочке after."""
    by_id = {item_id(item, n): item for n, item in enumerate(items)}
    # Снимок старше поля delay: считаем, что после сборки delay не меняли.
    old_snapshot = not isinstance(built, dict)
    done, rest = {}, {}

    def applied(ident, depth=0):
        if ident not in by_id or depth > len(items):
            return 0
        if ident not in done:
            item = by_id[ident]
            own = 0 if old_snapshot else (item.get("delay") or 0) - (built.get(ident) or 0)
            done[ident] = own + (applied(item["after"], depth + 1) if item.get("after") is not None else 0)
        return done[ident]

    for ident, one in result.items():
        item = by_id[ident]
        if item.get("always"):
            if one["shift"]:
                one["notes"].append("элемент на всё видео: сдвиг по времени не переносится")
            rest[ident] = 0
            continue
        left = one["shift"] - applied(ident)
        rest[ident] = left if abs(left) > tol["seconds"] else 0
        if rest[ident]:
            one["change"]["shift"] = rest[ident]
            if item.get("until_word") is not None:
                one["notes"].append("конец остаётся на слове until_word")
    for ident, one in result.items():
        target = by_id[ident].get("after")
        delta = rest[ident] - (rest.get(target, 0) if target is not None else 0)
        one["delay"] = delta if abs(delta) > tol["seconds"] else 0


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
        if one.get("layers"):
            parts.append("слои: " + ", ".join(
                f"{key} ({', '.join(PROP_NAMES[prop] for prop in props)})" for key, props in one["layers"].items()))
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
        if one and one.get("delay"):
            delay = round((item.get("delay") or 0) + one["delay"], 3)
            if delay:
                item["delay"] = delay
            else:
                item.pop("delay", None)
            count += 1
        change = {key: value for key, value in (one or {}).get("change", {}).items() if key != "shift"}
        if not one or not (change or one["order"] or one.get("layers")):
            continue
        adjust = dict(item.get("adjust") or {})
        stored = json.loads(json.dumps(adjust.get("layers") or {}))
        for key in one.get("roots", []):
            for prop, value in (stored.get(key) or {}).items():
                follow(prop, value, change)
        for key, props in one.get("layers", {}).items():
            stored.setdefault(key, {}).update(props)
        if stored:
            adjust["layers"] = stored
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


def follow(prop, value, change):
    """Сохранённые ключи корневого слоя идут за новой правкой всего элемента: движок ставит их после adjust."""
    def each(fn):
        if "keys" in value:
            for key in value["keys"]:
                key[1] = fn(key[1])
        else:
            value["v"] = fn(value["v"])

    if prop == "position" and change.get("offset"):
        each(lambda v: [round(x + y, 3) for x, y in zip(pad(v, len(v)), pad(change["offset"], len(v)))])
    elif prop == "scale" and change.get("scale"):
        each(lambda v: [round(x * change["scale"], 3) for x in v])
    elif prop == "rotation" and change.get("rotation"):
        each(lambda v: round(v + change["rotation"], 3))


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

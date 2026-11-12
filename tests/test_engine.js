// Проверяет расчёты движка без After Effects: подменяет только то, что нужно этим функциям.
const assert = require("assert");
const fs = require("fs");
const path = require("path");

global.$ = { global: {} };
global.File = function (name) { this.fsName = name; };
global.Timeline = { importOnce: (file) => ({ duration: /\/i$/.test(file.fsName) ? 3 : 1 }) };
new Function(fs.readFileSync(path.resolve(__dirname, "../plugin/ae/engine/engine.jsxinc"), "utf8"))();
const E = $.global.Engine;

let level = null;
let layers = [];
E.comp = { layers: { add: (footage) => {
    const layer = { source: footage, keys: [], audio: { audioLevels: {
        setValue: (v) => { level = v[0]; layer.level = v[0]; },
        setValueAtTime: (time, v) => { layer.keys.push([time, v[0]]); },
    } } };
    layers.push(layer);
    return layer;
} } };
E.assets = [
    { id: "quick", file: "a", audible: 0.1 },
    { id: "middle", file: "b", audible: 0.2 },
    { id: "long", file: "c", audible: 0.4 },
];
E.base = "/base";
E.current = "item";
E.tokens = {
    motion: { pop_duration: 0.5, lead: 0.1 },
    sounds: {
        level: -14, arrow: "quick", scan: "quick", text: null,
        appear: [{ max: 0.5, sound: "quick" }, { max: 1, sound: "middle" }, { sound: "long" }],
        levels: { scan: -20 },
    },
};

function played(name, t, length) {
    E.soundLog = [];
    E.sound({}, name, t, length);
    return E.soundLog[0];
}

const centred = played("arrow", 5, 0.3);
assert.ok(Math.abs((centred.start + centred.end) / 2 - 5.15) < 0.001, "звук по центру анимации");
assert.strictEqual(played("pop", 1, 0.3).sound, "quick");
assert.strictEqual(played("group", 1, 0.9).sound, "middle");
assert.strictEqual(played("group", 1, 1.4).sound, "long");
assert.strictEqual(played("text", 1, 0.3), undefined, "null отключает звук");
assert.strictEqual(played("arrow", 0, 0.01).start, 0, "звук не уходит левее нуля");
played("scan", 1, 0.2);
assert.strictEqual(level, -20, "своя громкость события");
played("arrow", 1, 0.2);
assert.strictEqual(level, -14, "общая громкость");
E.soundLog = [];
assert.strictEqual(E.sound({ sound: false }, "arrow", 1, 0.2).length, 0, "sound: false");


// Новый формат токенов: события, запасные имена, длины, серии, якорь, подложка.
const near = (a, b) => Math.abs(a - b) < 0.001;
E.assets = E.assets.concat([
    { id: "pop_s", file: "d", audible: 0.15, hit: 0.03 },
    { id: "pop_s2", file: "d2", audible: 0.15, hit: 0.03 },
    { id: "pop_m", file: "e", audible: 0.5, hit: 0.1 },
    { id: "pop_l", file: "f", audible: 1.2 },
    { id: "tick1", file: "g", audible: 0.1, hit: 0.01 },
    { id: "tick2", file: "h", audible: 0.1, hit: 0.01, loudness: -20 },
    { id: "shimmer", file: "i", audible: 3 },
    { id: "loud", file: "j", audible: 0.2, loudness: -10 },
]);
E.tokens.sounds = {
    level: -14, reference_loudness: -16, arrow: "quick",
    fallback: { pop: "appear", group: "appear", dim: null, swap: "missing" },
    events: {
        appear: {
            anchor: 0.5,
            tiers: ["pop_s", "pop_m", "pop_l"],
            variants: { pop_s: ["pop_s", "pop_s2"] },
            series: { sounds: ["tick1", "tick2"], min_hits: 3, min_span: 0.7, gap: 0.1, max_hits: 4, fade_db: -6, pitch_step: 0 },
            bed: { sound: "shimmer", min_span: 1.2, level: -10 },
        },
        move: { tiers: ["pop_m"], level: 3 },
        scan: "loud",
        mute: null,
    },
};

function play(name, t, length, hits, params) {
    E.soundLog = [];
    layers = [];
    E.sound(params || {}, name, t, length, hits);
    return E.soundLog;
}

let log = play("arrow", 5, 0.3);
assert.ok(near((log[0].start + log[0].end) / 2, 5.15), "старое имя без события: центр к центру");
assert.strictEqual(log[0].role, "single");
assert.strictEqual(log[0].series, null);
assert.strictEqual(play("swap", 1, 0.3).length, 0, "запасное имя без события и без старого звука: тишина");
assert.strictEqual(play("dim", 1, 0.3).length, 0, "null в fallback отключает");
assert.strictEqual(play("mute", 1, 0.3).length, 0, "null в events отключает");

assert.strictEqual(play("pop", 1, 0.6)[0].sound, "pop_m", "fallback и ближайшая длина");
assert.strictEqual(play("group", 1, 1.0)[0].sound, "pop_l", "ближайшая длина, а не порог");
log = play("move", 2, 0.4);
assert.ok(near((log[0].start + log[0].end) / 2, 2.2), "без anchor центр к центру");
assert.strictEqual(level, -11, "сдвиг события к общей громкости");
play("scan", 1, 0.2);
assert.strictEqual(level, -20, "громкий файл выровнен к опорной громкости");

E.current = "variants";
const picked = [play("appear", 1, 0.1)[0].sound, play("appear", 1, 0.1)[0].sound, play("appear", 1, 0.1)[0].sound];
assert.notStrictEqual(picked[0], picked[1], "варианты чередуются");
assert.strictEqual(picked[0], picked[2]);
E.current = "item";

log = play("appear", 2, 0.4);
assert.ok(near(log[0].hit, 2.2) && near(log[0].anchor, 2.2), "удар файла в якоре");
assert.ok(log[0].sound === "pop_m" && near(log[0].start, 2.1), "начало файла раньше якоря на время удара");

log = play("appear", 1, 0.9, [1.0, 1.05, 1.2, 1.3, 1.45, 1.6, 1.8]);
assert.ok(log.every((s) => s.role === "series"), "серия");
assert.strictEqual(new Set(log.map((s) => s.series)).size, 1, "один id серии");
assert.strictEqual(log.length, 4, "не больше max_hits");
assert.deepStrictEqual(log.map((s) => s.hit), [1.0, 1.3, 1.45, 1.8], "прореживание до gap и равномерный выбор");
assert.deepStrictEqual(log.map((s) => s.sound), ["tick1", "tick2", "tick1", "tick2"], "звуки серии по кругу");
assert.strictEqual(layers[0].level, -14);
assert.strictEqual(layers[3].level, -14 + 4 - 6, "к концу тише на fade_db, громкость выровнена");
assert.ok(layers.every((l) => l.stretch === undefined), "pitch_step 0 не растягивает");
E.tokens.sounds.events.appear.series.pitch_step = 1;
play("appear", 1, 0.9, [1.0, 1.3, 1.6]);
assert.ok(layers[0].stretch === undefined && layers[1].stretch < 100 && layers[2].stretch < layers[1].stretch, "тон растёт");
E.tokens.sounds.events.appear.series.pitch_step = 0;
assert.strictEqual(play("appear", 1, 0.5, [1.0, 1.2, 1.4])[0].role, "single", "короткая группа звучит одним звуком");
assert.strictEqual(play("appear", 1, 0.9, [1.0, 1.4])[0].role, "single", "мало элементов: один звук");

log = play("appear", 1, 2, [1.0, 1.5, 2.0]);
const bed = log.find((s) => s.role === "bed");
assert.ok(bed && bed.start === 1 && bed.end === 3, "подложка обрезана по длине анимации");
assert.strictEqual(layers[layers.length - 1].outPoint, 3, "конец подложки в слое");
log = play("appear", 1, 2);
assert.ok(log.some((s) => s.role === "single") && log.some((s) => s.role === "bed"), "подложка и под одиночным");
const bedLayer = layers[layers.length - 1];
assert.strictEqual(bedLayer.keys[0][1], -14 - 10, "свой уровень подложки");
assert.ok(bedLayer.keys[1][0] === 3 && bedLayer.keys[1][1] < -60, "затухание к концу");
assert.ok(!play("appear", 1, 1.0).some((s) => s.role === "bed"), "короткая анимация без подложки");

assert.strictEqual(play("appear", 1, 2, [1, 1.5, 2], { sound: false }).length, 0, "sound: false в новом формате");
assert.strictEqual(play("appear", 1, 0.4, null, { sound: "quick" })[0].sound, "quick", "блок называет свой звук");
E.soundLog = [];

E.timeline = { words: [{ start: 0 }, { start: 2 }] };
E.starts = { a: 3 };
assert.strictEqual(E.time({ at_word: 1 }), 1.9, "опережение слова");
assert.strictEqual(E.time({ after: "a", delay: 0.5 }), 3.5);
assert.strictEqual(E.time({ at: 4 }), 4);

// Пометки уходят вместе с целью.
global.CameraLayer = function () {};
global.LightLayer = function () {};
function fakeLayer(id, inPoint, outPoint, extra) {
    const keys = [];
    return Object.assign({
        comment: "scene:" + id, hasVideo: true, nullLayer: false, inPoint, outPoint, keys,
        opacity: { setValueAtTime: (t, v) => keys.push([t, v]), valueAtTime: () => 100 },
    }, extra || {});
}
const cells = fakeLayer("cells", 0, 6);
const mark = fakeLayer("mark", 2, 10);
const early = fakeLayer("short", 2, 4);
const rig = fakeLayer("rig", 2, 10, { nullLayer: true });
const onMark = fakeLayer("pointer", 3, 10);
const free = fakeLayer("free", 1, 10);
const sound = fakeLayer("mark", 2, 10, { hasVideo: false });
E.comp = { duration: 10, frameDuration: 1 / 30, numLayers: 0, layer: (n) => all[n - 1] };
const all = [cells, mark, early, rig, onMark, free, sound];
E.comp.numLayers = all.length;
E.tokens.motion.exit_duration = 0.2;
E.ends = {};
E.starts = { cells: 0, mark: 2, short: 2, rig: 2, pointer: 3, free: 1 };
assert.deepStrictEqual(E.targetsOf({ params: { target: "cells" } }), ["cells"]);
assert.deepStrictEqual(E.targetsOf({ params: { target: [10, 20] } }), [], "точка вместо id не цель");
assert.deepStrictEqual(E.targetsOf({ params: { targets: ["cells", "nope"] } }), ["cells"]);
E.endWithTargets([
    { id: "mark", targets: ["cells"], layers: [mark, sound] },
    { id: "short", targets: ["cells"], layers: [early] },
    { id: "rig", targets: ["cells"], layers: [rig] },
    { id: "pointer", targets: ["mark"], layers: [onMark] },
    { id: "free", targets: ["background"], layers: [free] },
]);
assert.strictEqual(mark.outPoint, 6, "пометка обрезана по концу цели");
assert.deepStrictEqual(mark.keys, [[5.8, 100], [6, 0]], "и уходит тем же затуханием");
assert.strictEqual(sound.outPoint, 10, "звук не обрезается");
assert.strictEqual(early.outPoint, 4, "свой более ранний конец остаётся");
assert.ok(rig.outPoint === 6 && rig.keys.length === 0, "нуль обрезается без затухания");
assert.strictEqual(onMark.outPoint, 6, "пометка на пометке уходит с первой целью");
assert.strictEqual(free.outPoint, 10, "цель до конца ролика: ничего не меняется");
E.ends = { cells: 3 };
const after = fakeLayer("late", 4, 10);
E.endWithTargets([{ id: "late", targets: ["cells"], layers: [after] }]);
assert.strictEqual(after.outPoint, 10, "слой, родившийся после ухода цели, не трогаем");
const exitMark = fakeLayer("m2", 1, 10);
E.endWithTargets([{ id: "m2", targets: ["cells"], layers: [exitMark] }]);
assert.strictEqual(exitMark.outPoint, 3, "уход, о котором сообщил блок, тоже конец цели");
E.ends = {};

// Родитель до позиции: позиция ставится в координатах родителя.
function posLayer(value, anchor, threeD) {
    const layer = { threeDLayer: !!threeD, parent: null,
        position: { value, setValue(v) { this.value = v; } }, anchorPoint: { value: anchor || [0, 0] } };
    layer.setParentWithJump = (p) => { layer.parent = p; };
    return layer;
}
const parent = posLayer([500, 300], [10, 20]);
const child = posLayer([560, 340]);
E.attach(child, parent);
assert.strictEqual(child.parent, parent);
assert.deepStrictEqual(child.position.value, [70, 60], "позиция в пространстве родителя от его якоря");
const deep = posLayer([0, 0, -2], null, true);
E.attach(deep, parent, [5, 6]);
assert.deepStrictEqual(deep.position.value, [5, 6, -2], "смещение задано явно, глубина сохраняется");


// Дополнение поверх замороженной части: ссылки store на слои повторной сборки меняются на настоящие.
global.AVLayer = function () {};
global.ShapeLayer = function () {};
global.TextLayer = function () {};
global.Property = function () {};
global.PropertyGroup = function () {};
global.MaskPropertyGroup = function () {};
global.PropertyType = { PROPERTY: 1, NAMED_GROUP: 2 };
global.PropertyValueType = { NO_VALUE: 0, CUSTOM_VALUE: 9, OneD: 1 };
global.KeyframeInterpolationType = { LINEAR: 1, BEZIER: 2, HOLD: 3 };
function aeLayer(id, name) {
    const layer = Object.create(AVLayer.prototype);
    Object.assign(layer, { id, name, propertyDepth: 0, matchName: "ADBE AV Layer", props: [] });
    layer.numProperties = 0;
    layer.property = (i) => layer.props[i - 1];
    return layer;
}
function aeProp(layer, index, match, name, value) {
    const prop = Object.create(Property.prototype);
    Object.assign(prop, {
        propertyDepth: 1, propertyIndex: index, matchName: match, name, parentProperty: layer, value,
        propertyType: PropertyType.PROPERTY, propertyValueType: PropertyValueType.OneD,
        canVaryOverTime: true, canSetExpression: true, expression: "", keys: [],
    });
    prop.propertyGroup = () => layer;
    Object.defineProperty(prop, "numKeys", { get: () => prop.keys.length });
    prop.keyTime = (k) => prop.keys[k - 1].t;
    prop.removeKey = (k) => { prop.keys.splice(k - 1, 1); };
    prop.setValueAtTime = (t, v) => {
        const at = prop.keys.findIndex((key) => Math.abs(key.t - t) < 1e-9);
        if (at >= 0) { prop.keys[at].v = v; return; }
        prop.keys.push({ t, v, out: KeyframeInterpolationType.BEZIER });
        prop.keys.sort((x, y) => x.t - y.t);
    };
    prop.nearestKeyIndex = (t) => {
        let best = 1;
        prop.keys.forEach((key, i) => { if (Math.abs(key.t - t) < Math.abs(prop.keys[best - 1].t - t)) best = i + 1; });
        return best;
    };
    prop.keyInInterpolationType = (k) => KeyframeInterpolationType.BEZIER;
    prop.setInterpolationTypeAtKey = (k, i, o) => { prop.keys[k - 1].out = o; };
    layer.props[index - 1] = prop;
    layer.numProperties = layer.props.length;
    return prop;
}
const tempComp = { name: "temp" };
const realComp = { name: "real" };
const tempCell = aeLayer(101, "cell 0");
const tempGone = aeLayer(102, "cell 1");
const tempInk = aeProp(tempCell, 1, "ADBE Fill-0002", "Color", 0);
const realCell = aeLayer(7, "cell 0 (renamed)");
const realInk = aeProp(realCell, 1, "ADBE Fill-0002", "Color", 0);
function TextDocument() { this.text = "a"; }
const doc = new TextDocument();
const store = { cells: [tempCell, tempGone], parts: [{ ink: tempInk }], comp: tempComp, x: 960, other: { cells: null }, doc };
store.self = store;
const missing = [];
E.remap(store, { 101: realCell, 102: null }, tempComp, realComp, missing);
assert.strictEqual(store.cells[0], realCell, "слой повторной сборки заменён настоящим по id");
assert.strictEqual(store.parts[0].ink, realInk, "свойство найдено по пути от слоя");
assert.strictEqual(store.comp, realComp, "композиция тоже");
assert.strictEqual(store.cells[1], null, "удалённый автором слой: null");
assert.deepStrictEqual(missing, ["cell 1"], "и его имя в списке потерь");
assert.strictEqual(store.x, 960);
assert.strictEqual(store.doc, doc, "объекты AE не обходятся");

// Заморозка: ключи до стыка не появляются, кадры до стыка остаются прежними.
const frozenLayer = aeLayer(9, "box");
Object.assign(frozenLayer, { inPoint: 0, outPoint: 20 });
const still = aeProp(frozenLayer, 1, "ADBE Position", "Position", 500);
const keyed = aeProp(frozenLayer, 2, "ADBE Opacity", "Opacity", 0);
keyed.setValueAtTime(1, 0);
keyed.setValueAtTime(2, 100);
const later = aeProp(frozenLayer, 3, "ADBE Scale", "Scale", 100);
const expr = aeProp(frozenLayer, 4, "ADBE Rotate Z", "Rotation", 0);
const snap = E.freezeSnapshot([frozenLayer]);
// Новая часть со стыком в 10 с: блок ставит ключи и до стыка, и после.
still.setValueAtTime(9.99, 700);
still.setValueAtTime(10, 700);
still.setValueAtTime(11, 900);
keyed.setValueAtTime(10.5, 0);
later.setValueAtTime(12, 50);
later.setValueAtTime(13, 100);
expr.expression = "wiggle(1, 5)";
frozenLayer.inPoint = 3;
const fixes = E.freezeFix(snap, 10);
assert.ok(still.keys.every((k) => k.t >= 10 - 0.0011), "ключей до стыка нет");
assert.ok(still.keys[0].v === 500 && still.keys[0].out === KeyframeInterpolationType.HOLD && still.keys[0].t < 10,
    "неподвижное свойство держит старое значение до новых ключей");
assert.deepStrictEqual(still.keys.slice(1).map((k) => [k.t, k.v]), [[10, 700], [11, 900]], "новая анимация с места стыка");
assert.strictEqual(keyed.keys[1].out, KeyframeInterpolationType.HOLD, "старый последний ключ держится до новых");
assert.ok(later.keys[0].t === 10 && later.keys[0].v === 100 && later.keys[0].out === KeyframeInterpolationType.HOLD,
    "ключи только после стыка: значение до них прежнее");
assert.ok(/^if \(time < 10\) \{\nvalue\n\} else \{\nwiggle/.test(expr.expression), "новое выражение работает со стыка");
assert.strictEqual(frozenLayer.inPoint, 0, "начало слоя прежнее");
assert.ok(fixes.length >= 5);


// adjust: ручные правки автора после блока, на корневых слоях; restack: порядок слоёв под или над другим элементом.
function prop(value, keys) {
    return {
        value: value, keys: keys ? keys.slice(0) : null,
        get numKeys() { return this.keys ? this.keys.length : 0; },
        keyValue(k) { return this.keys[k - 1]; },
        setValueAtKey(k, v) { this.keys[k - 1] = v; },
        valueAtTime() { return this.value; },
        setValue(v) { this.value = v; },
    };
}
function adjLayer(id, parent, position, opts) {
    const props = {
        "ADBE Position": Object.assign(position, { dimensionsSeparated: false }),
        "ADBE Scale": prop([100, 100, 100], opts && opts.scaleKeys),
        "ADBE Rotate Z": prop(0),
    };
    return { id, parent, hasVideo: !(opts && opts.audio), property: () => ({ property: (name) => props[name] }), props };
}
const rootLayer = adjLayer(1, null, prop([100, 200, 0]), { scaleKeys: [[0, 0, 0], [100, 100, 100]] });
const childLayer = adjLayer(2, rootLayer, prop([10, 10, 0]));
const keyedLayer = adjLayer(3, { id: 99 }, prop(null, [[0, 0, 0], [50, 50, 0]]));
const soundLayer = adjLayer(4, null, prop([0, 0, 0]), { audio: true });
E.adjust([rootLayer, childLayer, keyedLayer, soundLayer], { offset: [5, -10], scale: 0.5, rotation: 3 });
assert.deepStrictEqual(rootLayer.props["ADBE Position"].value, [105, 190, 0], "сдвиг корня");
assert.deepStrictEqual(rootLayer.props["ADBE Scale"].keys, [[0, 0, 0], [50, 50, 50]], "масштаб по всем ключам");
assert.strictEqual(rootLayer.props["ADBE Rotate Z"].value, 3, "поворот");
assert.deepStrictEqual(childLayer.props["ADBE Position"].value, [10, 10, 0], "ребёнок идёт за родителем");
assert.deepStrictEqual(keyedLayer.props["ADBE Position"].keys, [[5, -10, 0], [55, 40, 0]], "родитель из другого элемента: слой корневой");
assert.deepStrictEqual(soundLayer.props["ADBE Position"].value, [0, 0, 0], "звук не трогаем");
E.adjust([rootLayer], null);
const sepProps = { "ADBE Position": { dimensionsSeparated: true }, "ADBE Position_0": prop(10), "ADBE Position_1": prop(null, [20, 30]) };
E.adjust([{ id: 7, parent: null, hasVideo: true, property: () => ({ property: (n) => sepProps[n] }) }], { offset: [1, 2] });
assert.ok(sepProps["ADBE Position_0"].value === 11 && sepProps["ADBE Position_1"].keys.join() === "22,32", "раздельная позиция");

const stack = [];
function stacked(name) {
    const one = {
        name,
        moveAfter(other) { stack.splice(stack.indexOf(one), 1); stack.splice(stack.indexOf(other) + 1, 0, one); },
        moveBefore(other) { stack.splice(stack.indexOf(one), 1); stack.splice(stack.indexOf(other), 0, one); },
    };
    stack.push(one);
    return one;
}
const [z1, z2, o1, o2, bg] = ["z1", "z2", "o1", "o2", "bg"].map(stacked);
E.restack([z1, z2], [o1, o2], "below");
assert.deepStrictEqual(stack.map((l) => l.name), ["o1", "o2", "z1", "z2", "bg"], "под нижним слоем, свой порядок");
E.restack([bg], [o1, o2], "above");
assert.deepStrictEqual(stack.map((l) => l.name), ["bg", "o1", "o2", "z1", "z2"], "над верхним слоем");
E.restack([], [o1], "below");

// Обрезка слоёв, которые окончательно ушли в 0: всё, что может вернуть видимость или читать слой, её запрещает.
const zero = { speed: 0, influence: 33 };
function opacityProp(keys, expression) {
    const prop = Object.create(Property.prototype);
    Object.assign(prop, {
        name: "Opacity", propertyType: PropertyType.PROPERTY, propertyValueType: PropertyValueType.OneD,
        canSetExpression: true, expression: expression || "", expressionEnabled: !!expression, keys: keys.slice(0),
    });
    Object.defineProperty(prop, "numKeys", { get: () => prop.keys.length });
    prop.keyTime = (k) => prop.keys[k - 1][0];
    prop.keyValue = (k) => prop.keys[k - 1][1];
    prop.keyOutInterpolationType = (k) => prop.keys[k - 1][2] || KeyframeInterpolationType.BEZIER;
    prop.keyInInterpolationType = () => KeyframeInterpolationType.BEZIER;
    prop.keyOutTemporalEase = (k) => [prop.keys[k - 1][3] || zero];
    prop.keyInTemporalEase = (k) => [prop.keys[k - 1][4] || zero];
    return prop;
}
let nextId = 500;
function trimLayer(item, name, keys, extra) {
    const opacity = opacityProp(keys || [], extra && extra.expression);
    const layer = Object.assign({
        id: nextId++, name, comment: item === null ? "" : "scene:" + item, hasVideo: true, nullLayer: false,
        enabled: true, isTrackMatte: false, locked: false, inPoint: 0, outPoint: 30, parent: null, opacity,
        props: [opacity],
    }, extra || {});
    layer.numProperties = layer.props.length;
    layer.property = (i) => layer.props[i - 1];
    return layer;
}
function exprProp(text) {
    return Object.assign(Object.create(Property.prototype), {
        name: "Position", propertyType: PropertyType.PROPERTY, propertyValueType: PropertyValueType.OneD,
        canSetExpression: true, expression: text, expressionEnabled: true, numKeys: 0,
    });
}
function trimComp(list) {
    return { numLayers: list.length, layer: (n) => list[n - 1], frameDuration: 0.1, duration: 30 };
}
const gone = [[1, 0], [1.5, 100], [4, 100], [4.5, 0]];

assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", gone)), 4.5, "конец последнего ухода в 0");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", [[1, 0], [2, 100]])), null, "виден до конца");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", [[1, 0], [2, 0]])), null, "ни разу не виден: не трогаем");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", [])), null, "без ключей");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", gone, { expression: "value * 0.6" })), 4.5, "умножение на число сохраняет 0");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", gone, { expression: "(value * 0.5) * 0.8;" })), 4.5, "вложенное умножение");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", gone, { expression: "100" })), null, "выражение на opacity: не уверены");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", gone.concat([[6, 0, null, null, { speed: 40 }]]))), null,
    "кривая между нулями может выйти выше 0");
assert.strictEqual(E.hiddenAfter(trimLayer("a", "x", gone.concat([[6, 0, KeyframeInterpolationType.HOLD], [7, 0]]))), 4.5);

assert.deepStrictEqual(E.layerRefs('thisComp.layer("box \\"1\\"").position + thisComp.layer(3).scale'),
    { names: ['box "1"'], indices: [3], unknown: false });
assert.ok(E.layerRefs("thisComp.layer(index - 1).position").unknown, "ссылка не по имени и не по номеру: читает любой слой");
assert.ok(!E.layerRefs("thisLayer.position").unknown);

{
    const plainGone = trimLayer("a", "plain", gone);
    const back = trimLayer("b", "back", [[1, 0], [1.5, 100], [4, 100], [4.5, 0], [8, 0], [8.5, 100], [10, 100], [10.5, 0]]);
    const stays = trimLayer("c", "stays", [[1, 0], [1.5, 100]]);
    const voice = trimLayer(null, "voice", [], { hasVideo: false });
    const sfx = trimLayer("a", "pop", [], { hasVideo: false });
    const ended = trimLayer("d", "ended", gone, { outPoint: 4.5 });
    const list = [plainGone, back, stays, voice, sfx, ended];
    const cut = E.trimHidden(trimComp(list), [], 0.5);
    assert.ok(near(plainGone.outPoint, 5), "ушёл в 0: обрезан по концу ухода плюс запас");
    assert.ok(near(back.outPoint, 11), "уход и возврат: режем только после последнего ухода");
    assert.strictEqual(stays.outPoint, 30, "видимый до конца не трогаем");
    assert.strictEqual(voice.outPoint, 30, "голос не трогаем");
    assert.strictEqual(sfx.outPoint, 30, "звук элемента не трогаем");
    assert.strictEqual(ended.outPoint, 4.5, "раньше уже обрезанный не удлиняем");
    assert.deepStrictEqual(cut.map((c) => [c.item, c.layer, c.from, c.to, c.in_from, c.in_to]), [
        ["a", "plain", 30, 5, 0, 0.5], ["b", "back", 30, 11, 0, 0.5], ["c", "stays", undefined, undefined, 0, 0.5],
        ["d", "ended", undefined, undefined, 0, 0.5]]);
}
{
    const parent = trimLayer("a", "parent", gone);
    const child = trimLayer("b", "child", [[2, 0], [3, 100], [12, 100], [12.5, 0]], { parent });
    const lone = trimLayer("c", "lone", gone);
    const keeper = trimLayer("c", "keeper", [[2, 0], [3, 100]], { parent: lone });
    E.trimHidden(trimComp([parent, child, lone, keeper]), [], 0.5);
    assert.ok(near(child.outPoint, 13), "ребёнок обрезан по своему уходу");
    assert.ok(near(parent.outPoint, 13), "родитель не раньше ребёнка");
    assert.strictEqual(lone.outPoint, 30, "родитель видимого ребёнка остаётся");
}
{
    const rig = trimLayer("move", "rig", [], { nullLayer: true });
    const a = trimLayer("a", "a", gone, { parent: rig });
    const b = trimLayer("b", "b", [[1, 0], [2, 100], [6, 100], [6.5, 0]], { parent: rig });
    const idle = trimLayer("x", "idle null", [], { nullLayer: true });
    E.trimHidden(trimComp([rig, a, b, idle]), [{ targets: ["a", "b"], layers: [rig] }], 0.5);
    assert.ok(near(a.outPoint, 7) && near(b.outPoint, 7), "цели move уходят вместе: по последнему уходу набора");
    assert.ok(near(rig.outPoint, 7), "нуль живёт, пока живут его дети");
    assert.strictEqual(idle.outPoint, 30, "нуль без детей и читателей не трогаем");
}
{
    const target = trimLayer("cells", "cell", gone);
    const connector = trimLayer("link", "line", [[9, 0], [9.5, 100]], { inPoint: 9 });
    const shortMark = trimLayer("mark", "ring", [[2, 0], [2.5, 100], [6, 100], [6.5, 0]], { inPoint: 2 });
    const target2 = trimLayer("cells2", "cell2", gone);
    E.trimHidden(trimComp([target, connector, target2, shortMark]), [
        { targets: ["cells"], layers: [connector] },
        { targets: ["cells2"], layers: [shortMark] },
    ], 0.5);
    assert.strictEqual(target.outPoint, 30, "цель позднего connector, который виден до конца, остаётся");
    assert.ok(near(target2.outPoint, 7) && near(shortMark.outPoint, 7), "цель живёт, пока живёт пометка на ней");
}
{
    const source = trimLayer("dot", "dot 1", gone);
    const reader = trimLayer("readout", "label", [[1, 0], [2, 100]], {});
    reader.props.push(exprProp('var p = thisComp.layer("dot 1").transform.position; p'));
    reader.numProperties = reader.props.length;
    const byIndex = trimLayer("dot2", "dot 2", gone);
    const indexReader = trimLayer("cursor", "cursor", [[1, 0], [2, 100], [8, 100], [8.5, 0]]);
    indexReader.props.push(exprProp("thisComp.layer(3).toWorld([0, 0, 0])"));
    indexReader.numProperties = indexReader.props.length;
    E.trimHidden(trimComp([source, reader, byIndex, indexReader]), [], 0.5);
    assert.strictEqual(source.outPoint, 30, "слой, который читает выражение видимого слоя, остаётся");
    assert.ok(near(byIndex.outPoint, 9) && near(indexReader.outPoint, 9), "ссылка по номеру: не раньше читателя");
}
{
    const any = trimLayer("a", "a", gone);
    const wild = trimLayer("w", "wild", [[1, 0], [2, 100]]);
    wild.props.push(exprProp("thisComp.layer(index + 1).position"));
    wild.numProperties = wild.props.length;
    E.trimHidden(trimComp([any, wild]), [], 0.5);
    assert.strictEqual(any.outPoint, 30, "выражение читает неизвестный слой: ничего не режем раньше него");
}
{
    const matte = trimLayer("a", "matte", gone, { isTrackMatte: true });
    const off = trimLayer("a", "off", gone, { enabled: false });
    const locked = trimLayer("a", "locked", gone, { locked: true });
    const camera = Object.assign(Object.create(CameraLayer.prototype), trimLayer("cam", "camera", gone));
    const foreign = trimLayer(null, "author layer", gone);
    E.trimHidden(trimComp([matte, off, locked, camera, foreign]), [], 0.5);
    assert.ok([matte, off, locked, camera, foreign].every((l) => l.outPoint === 30),
        "маски, выключенные, заблокированные, камеры и слои не из сцены не трогаем");
}
{
    PropertyValueType.LAYER_INDEX = 7;
    const map = trimLayer("a", "map", gone);
    const user = trimLayer("b", "user", [[1, 0], [2, 100]]);
    user.props.push(Object.assign(Object.create(Property.prototype), {
        propertyType: PropertyType.PROPERTY, propertyValueType: 7, value: 1, canSetExpression: false,
    }));
    user.numProperties = user.props.length;
    E.trimHidden(trimComp([map, user]), [], 0.5);
    assert.strictEqual(map.outPoint, 30, "слой-параметр эффекта видимого слоя остаётся");
    delete PropertyValueType.LAYER_INDEX;
}
{
    const odd = trimLayer("a", "odd", [[1, 0], [1.5, 100], [4, 100], [4.43, 0]]);
    E.trimHidden(trimComp([odd]), [], 0.5);
    assert.ok(near(odd.outPoint, 5), "конец обрезки округлён вверх до кадра");
}
{
    // Дописывание: что обрезала прошлая сборка, возвращается, если автор не двигал конец слоя.
    const kept = trimLayer("a", "kept", gone, { outPoint: 5 });
    const moved = trimLayer("b", "moved", gone, { outPoint: 8 });
    const comp = trimComp([kept, moved]);
    const restored = E.untrim(comp, [{ id: kept.id, from: 30, to: 5 }, { id: moved.id, from: 30, to: 5 }, { id: 1, from: 30, to: 5 }]);
    assert.strictEqual(restored, 1);
    assert.strictEqual(kept.outPoint, 30, "обрезанный сборкой слой вернулся");
    assert.strictEqual(moved.outPoint, 8, "конец, который автор двигал, не трогаем");
    // Новая часть берёт старый слой целью и возвращает его ключами: он больше не обрезается.
    kept.opacity.keys.push([20, 0], [20.5, 100]);
    const fresh = trimLayer("new", "new", [[20, 0], [20.5, 100]], { inPoint: 20 });
    E.trimHidden(comp, [{ targets: ["a"], layers: [fresh] }], 0.5);
    assert.strictEqual(kept.outPoint, 30, "старый слой, который снова нужен, не обрезан");
}

// Обрезка начала: слой появляется только тогда, когда становится видимым.
assert.strictEqual(E.hiddenBefore(trimLayer("a", "x", gone)), 1, "последний ноль перед первым появлением");
assert.strictEqual(E.hiddenBefore(trimLayer("a", "x", [[1, 100], [2, 0]])), null, "виден с самого начала");
assert.strictEqual(E.hiddenBefore(trimLayer("a", "x", [[0, 0], [1, 0, null, null, { speed: 30 }], [2, 100]])), null,
    "кривая между нулями до появления может выйти выше 0");
assert.strictEqual(E.hiddenBefore(trimLayer("a", "x", gone, { expression: "100" })), null, "выражение на opacity");
{
    const late = trimLayer("a", "late", [[5, 0], [5.5, 100]]);
    const parent = trimLayer("b", "parent", [[6, 0], [6.5, 100]]);
    const child = trimLayer("c", "child", [[3, 0], [3.5, 100]], { parent });
    const voice = trimLayer(null, "voice", [], { hasVideo: false });
    const rig = trimLayer("m", "rig", [], { nullLayer: true });
    const kid = trimLayer("k", "kid", [[8, 0], [8.5, 100]], { parent: rig });
    const target = trimLayer("t", "cell", [[10, 0], [10.5, 100]]);
    const user = trimLayer("u", "ring", [[4, 0], [4.5, 100]]);
    const reader = trimLayer("r", "label", [[2, 0], [2.5, 100]]);
    reader.props.push(exprProp('thisComp.layer("cell").transform.position'));
    reader.numProperties = reader.props.length;
    const cut = E.trimHidden(trimComp([late, parent, child, voice, rig, kid, target, user, reader]),
        [{ targets: ["t"], layers: [user] }], 0.5);
    assert.ok(near(late.inPoint, 4.5), "начало слоя за запас до первого появления");
    assert.ok(near(child.inPoint, 2.5) && near(parent.inPoint, 2.5), "родитель начинается не позже ребёнка");
    assert.strictEqual(voice.inPoint, 0, "звук не трогаем");
    assert.ok(near(kid.inPoint, 7.5) && near(rig.inPoint, 7.5), "нуль начинается со своим ребёнком");
    assert.ok(near(target.inPoint, 1.5), "цель начинается не позже элемента и выражения, которые её читают");
    assert.strictEqual(cut.find((c) => c.layer === "late").to, undefined, "конец не менялся: пары from, to нет");
    const comp = trimComp([late]);
    assert.strictEqual(E.untrim(comp, cut.filter((c) => c.layer === "late")), 1);
    assert.strictEqual(late.inPoint, 0, "дописывание возвращает прежнее начало");
}
{
    const odd = trimLayer("a", "odd", [[1.27, 0], [1.5, 100]]);
    E.trimHidden(trimComp([odd]), [], 0.5);
    assert.ok(near(odd.inPoint, 0.7), "начало округлено вниз до кадра");
    const wide = trimLayer("a", "wide", [[0.3, 0], [0.5, 100]]);
    E.trimHidden(trimComp([wide]), [], 0.5);
    assert.strictEqual(wide.inPoint, 0, "запас длиннее начала: не трогаем");
}

// Звук объектов вне кадра не ставится; у серии выпадают только удары элементов вне кадра.
{
    const boxes = {};
    E.screenBox = (layer, t) => boxes[layer.name];
    function visual(item, name, keys, box) {
        const layer = trimLayer(item, name, keys);
        layer.opacity.valueAtTime = (t) => {
            let v = keys.length ? keys[0][1] : 100;
            keys.forEach(([kt, kv]) => { if (t >= kt) v = kv; });
            return v;
        };
        boxes[name] = box;
        return layer;
    }
    function soundLayer(entry) {
        const layer = trimLayer(entry.item, "snd " + entry.sound, [], { hasVideo: false });
        layer.remove = () => { layer.removed = true; };
        E.soundLayers[layer.id] = entry;
        E.soundLog.push(entry);
        return layer;
    }
    const inside = [100, 100, 300, 200];
    const outside = [2100, 100, 2300, 200];
    const asBox = (b) => b && { left: b[0], top: b[1], right: b[2], bottom: b[3] };
    Object.keys(boxes).forEach((k) => delete boxes[k]);
    E.soundLog = [];
    E.soundLayers = {};
    const seen = visual("seen", "seen box", [[1, 0], [1.2, 100]], asBox(inside));
    const away = visual("away", "away box", [[1, 0], [1.2, 100]], asBox(outside));
    const half = [visual("half", "half in", [[1, 0], [1.2, 100]], asBox(inside)), visual("half", "half out", [[1, 0], [1.2, 100]], asBox(outside))];
    const cells = [0, 1, 2, 3].map((k) => visual("row", "cell " + k, [[1 + k * 0.5, 0], [1.1 + k * 0.5, 100]],
        asBox(k < 2 ? inside : outside)));
    const ghost = visual("ghost", "ghost", [[1, 0], [5, 0]], asBox(outside));
    const unknown = visual("unknown", "unknown", [[1, 0], [1.2, 100]], null);
    const single = (item) => ({ item, sound: item, event: "pop", role: "single", series: null, hit: 1.5,
        animation_start: 1, animation_end: 2 });
    const sounds = ["seen", "away", "half", "ghost", "unknown"].map((item) => soundLayer(single(item)));
    const strikes = [0, 1, 2, 3].map((k) => soundLayer({ item: "row", sound: "tick", event: "group", role: "series",
        series: "row/0", hit: 1.15 + k * 0.5, animation_start: 1, animation_end: 3 }));
    const voice = trimLayer(null, "voice", [], { hasVideo: false });
    const list = [seen, away, ...half, ...cells, ghost, unknown, ...sounds, ...strikes, voice];
    const comp = Object.assign(trimComp(list), { width: 1920, height: 1080, frameDuration: 0.04 });
    const muted = E.muteOffscreen(comp, { seen: [seen], away: [away], half, row: cells, ghost: [ghost], unknown: [unknown] }, 0);
    const gone = (l) => !!l.removed;
    assert.deepStrictEqual(sounds.map(gone), [false, true, false, false, false],
        "в кадре звучит, вне кадра нет, частично в кадре звучит, невидимый и неизвестный остаются");
    assert.deepStrictEqual(strikes.map(gone), [false, false, true, true], "серия теряет только удары элементов вне кадра");
    assert.deepStrictEqual(muted.map((m) => [m.item, m.at]), [["away", 1.5], ["row", 2.15], ["row", 2.65]]);
    assert.strictEqual(E.soundLog.length, 6, "убранные звуки уходят и из журнала");
    assert.ok(!voice.removed, "голос не трогаем");
    E.muteOffscreen(comp, { away: [away] }, -10);
    assert.ok(!E.soundLog.some((e) => e.item === "away"));
    boxes["seen box"] = { left: -195, top: 100, right: -5, bottom: 200 };
    E.soundLog = []; E.soundLayers = {};
    const edge = soundLayer(single("seen"));
    E.muteOffscreen(Object.assign(trimComp([seen, edge]), { width: 1920, height: 1080, frameDuration: 0.04 }), { seen: [seen] }, -10);
    assert.ok(!edge.removed, "отрицательный порог: объект рядом с краем ещё считается видимым");
    E.soundLog = []; E.soundLayers = {};
}

console.log("Движок в порядке");

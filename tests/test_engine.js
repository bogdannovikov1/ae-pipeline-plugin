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
console.log("Движок в порядке");

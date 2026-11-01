// Проверяет расчёты движка без After Effects: подменяет только то, что нужно этим функциям.
const assert = require("assert");
const fs = require("fs");
const path = require("path");

global.$ = { global: {} };
global.File = function (name) { this.fsName = name; };
global.Timeline = { importOnce: () => ({}) };
new Function(fs.readFileSync(path.resolve(__dirname, "../plugin/ae/engine/engine.jsxinc"), "utf8"))();
const E = $.global.Engine;

let level = null;
E.comp = { layers: { add: () => ({ source: { duration: 1 }, audio: { audioLevels: { setValue: (v) => { level = v[0]; } } } }) } };
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

E.timeline = { words: [{ start: 0 }, { start: 2 }] };
E.starts = { a: 3 };
assert.strictEqual(E.time({ at_word: 1 }), 1.9, "опережение слова");
assert.strictEqual(E.time({ after: "a", delay: 0.5 }), 3.5);
assert.strictEqual(E.time({ at: 4 }), 4);
console.log("Движок в порядке");

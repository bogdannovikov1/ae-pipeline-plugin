// Проверяет синтаксис JSX движка и всех блоков стартовой базы без After Effects.
const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..", "plugin");

function files(folder) {
    return fs.readdirSync(folder, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(folder, entry.name);
        return entry.isDirectory() ? files(full) : /\.jsx(inc)?$/.test(entry.name) ? [full] : [];
    });
}

let failed = 0;
for (const file of [...files(path.join(root, "ae")), ...files(path.join(root, "starter_base"))]) {
    const source = fs.readFileSync(file, "utf8").replace(/^#include.*$/gm, "");
    try {
        new Function(source);
    } catch (error) {
        failed += 1;
        console.log(`${file}: ${error.message}`);
    }
}
console.log(failed ? `Ошибок: ${failed}` : "Синтаксис в порядке");
process.exit(failed ? 1 : 0);

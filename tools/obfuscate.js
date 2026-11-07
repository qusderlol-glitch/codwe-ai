// Собирает «зашифрованную» копию интерфейса: web/ -> web_dist/. Запуск: node tools/obfuscate.js (см. tools/build_dist.py).
// Читаемые исходники остаются в web/ (в режиме разработчика отдаются они), пользователям отдаётся web_dist/.
const fs = require("fs");
const path = require("path");
const JSO = require(path.join(__dirname, "node_obf", "node_modules", "javascript-obfuscator"));

const SRC = path.join(__dirname, "..", "web");
const DST = path.join(__dirname, "..", "web_dist");

const common = {
  target: "browser",
  compact: true,
  renameGlobals: false,            // файлы делят общие глобальные имена, их переименовывать нельзя
  identifierNamesGenerator: "hexadecimal",
  stringArray: true,
  stringArrayEncoding: ["rc4"],    // строки хранятся зашифрованными и расшифровываются при запуске
  stringArrayThreshold: 0.85,
  stringArrayRotate: true,
  stringArrayShuffle: true,
  stringArrayWrappersCount: 2,
  stringArrayWrappersType: "function",
  numbersToExpressions: true,
  simplify: true,
  splitStrings: false,
  transformObjectKeys: false,
  unicodeEscapeSequence: false,
  selfDefending: false,
  debugProtection: false,
  disableConsoleOutput: false,
  seed: 0,
};
// страж — самый защищённый (запутывание потока управления + «мёртвый» код); остальное легче, чтобы не тормозило
const strong = { ...common, controlFlowFlattening: true, controlFlowFlatteningThreshold: 0.7, deadCodeInjection: true, deadCodeInjectionThreshold: 0.25 };

fs.rmSync(DST, { recursive: true, force: true });
fs.mkdirSync(DST, { recursive: true });
let n = 0;
for (const f of fs.readdirSync(SRC)) {
  const from = path.join(SRC, f), to = path.join(DST, f);
  if (!fs.statSync(from).isFile()) continue;
  if (f.endsWith(".js")) {
    const code = fs.readFileSync(from, "utf8");
    const opt = f === "guard.js" || f === "auth.js" ? strong : common;
    fs.writeFileSync(to, JSO.obfuscate(code, opt).getObfuscatedCode(), "utf8");
    n++;
  } else {
    fs.copyFileSync(from, to);
  }
}
console.log("obfuscated js files:", n);

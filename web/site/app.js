// VeriCode playground: runs vericode.engine.run_check in the browser via Pyodide.
"use strict";

const EXAMPLES = [
  {
    id: "fake",
    label: "Made-up package",
    expect: "BLOCK",
    files: {
      "main.py": `from fastapi import FastAPI
from fastapi_auth_helpers import require_api_key

app = FastAPI()


@app.get("/items")
@require_api_key
def list_items():
    return []
`,
      "requirements.txt": "fastapi\n",
    },
  },
  {
    id: "typo",
    label: "Typo of a real package",
    expect: "BLOCK",
    files: {
      "main.py": `import reqeusts


def fetch_user(user_id: int) -> dict:
    resp = reqeusts.get(f"https://api.example.com/users/{user_id}", timeout=10)
    resp.raise_for_status()
    return resp.json()
`,
      "requirements.txt": "requests\n",
    },
  },
  {
    id: "version",
    label: "Version that doesn't exist",
    expect: "BLOCK",
    files: {
      "main.py": `import pandas as pd
import requests


def load_prices(url: str) -> pd.DataFrame:
    data = requests.get(url, timeout=10).json()
    return pd.DataFrame(data["prices"])
`,
      "requirements.txt": "pandas==2.2.2\nrequests==99.0.0\n",
    },
  },
  {
    id: "clean",
    label: "Clean code (tricky names)",
    expect: "PASS",
    files: {
      "main.py": `import json
from pathlib import Path

import cv2  # PyPI package: opencv-python
import yaml  # PyPI package: PyYAML
from bs4 import BeautifulSoup  # PyPI package: beautifulsoup4
from PIL import Image  # PyPI package: Pillow
from sklearn.linear_model import LinearRegression  # PyPI package: scikit-learn

try:
    import ujson as fastjson  # optional speed-up, fine if missing
except ImportError:
    fastjson = json


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def thumbnail(src: Path, dst: Path) -> None:
    with Image.open(src) as img:
        img.thumbnail((256, 256))
        img.save(dst)


def page_title(html: str) -> str:
    return BeautifulSoup(html, "html.parser").title.string


def edges(src: Path):
    return cv2.Canny(cv2.imread(str(src)), 100, 200)


model = LinearRegression()
`,
      "requirements.txt": "opencv-python\nPyYAML\nbeautifulsoup4\nPillow\nscikit-learn\n",
    },
  },
];

const PYODIDE_PACKAGES = ["httpx", "pyyaml", "packaging", "platformdirs"];
const PY_DIR = "/home/pyodide/vericode_bundle";

const $ = (id) => document.getElementById(id);
const codeEl = $("code");
const gutterEl = $("gutter");
const runBtn = $("run");
const statusEl = $("status");
const resultsEl = $("results");

const files = { "main.py": "", "requirements.txt": "" };
let current = "main.py";
let vericode = null; // Python module vericode_web, once loaded
let running = false;

// ------------------------------------------------------------------ editor

function renderGutter() {
  const n = codeEl.value.split("\n").length;
  let s = "";
  for (let i = 1; i <= n; i++) s += i + "\n";
  gutterEl.textContent = s;
  gutterEl.scrollTop = codeEl.scrollTop;
}

function showFile(name) {
  files[current] = codeEl.value;
  current = name;
  codeEl.value = files[name];
  for (const t of document.querySelectorAll(".tab")) {
    t.setAttribute("aria-selected", String(t.dataset.file === name));
  }
  codeEl.scrollTop = 0;
  renderGutter();
}

function goToLine(file, line) {
  if (!(file in files)) return;
  showFile(file);
  const lines = codeEl.value.split("\n");
  const ln = Math.max(1, Math.min(line || 1, lines.length));
  let start = 0;
  for (let i = 0; i < ln - 1; i++) start += lines[i].length + 1;
  codeEl.focus();
  codeEl.setSelectionRange(start, start + lines[ln - 1].length);
  const lineHeight = parseFloat(getComputedStyle(codeEl).lineHeight) || 21;
  codeEl.scrollTop = Math.max(0, (ln - 4) * lineHeight);
  renderGutter();
}

codeEl.addEventListener("input", () => {
  files[current] = codeEl.value;
  markExample(null);
  renderGutter();
});
codeEl.addEventListener("scroll", () => { gutterEl.scrollTop = codeEl.scrollTop; });
codeEl.addEventListener("keydown", (e) => {
  if (e.key === "Tab" && !e.shiftKey) {
    e.preventDefault();
    codeEl.setRangeText("    ", codeEl.selectionStart, codeEl.selectionEnd, "end");
    codeEl.dispatchEvent(new Event("input"));
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    runCheck();
  }
});
for (const t of document.querySelectorAll(".tab")) {
  t.addEventListener("click", () => showFile(t.dataset.file));
}

// ------------------------------------------------------------------ examples

function markExample(id) {
  for (const b of document.querySelectorAll(".example")) {
    b.setAttribute("aria-pressed", String(b.dataset.id === id));
  }
}

function loadExample(ex) {
  Object.assign(files, ex.files);
  current = "main.py";
  codeEl.value = files["main.py"];
  showFile("main.py");
  markExample(ex.id);
  clearResults(`"${ex.label}" loaded`);
}

// Results always belong to the code on screen: a new example clears the old result and the
// user presses Check to see the new one.
function clearResults(title = "No check yet") {
  resultsEl.className = "empty";
  resultsEl.replaceChildren(el("p", "empty-title", title));
  const p = el("p");
  p.append("Press ", el("strong", null, "Check"), " (", el("kbd", null, "Ctrl"), "+",
    el("kbd", null, "Enter"), ") to verify this code.");
  resultsEl.append(p);
}

for (const ex of EXAMPLES) {
  const b = document.createElement("button");
  b.className = "example";
  b.dataset.id = ex.id;
  b.setAttribute("aria-pressed", "false");
  b.title = `Expected result: ${ex.expect}`;
  b.innerHTML = `<span class="dot ${ex.expect}"></span>`;
  b.append(ex.label);
  b.addEventListener("click", () => loadExample(ex));
  $("examples").append(b);
}

// ------------------------------------------------------------------ results

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

const SUMMARY = {
  PASS: "Every import and pin checks out on PyPI.",
  WARN: "Nothing to block, but some findings need a look.",
  BLOCK: "This code depends on something that doesn't exist.",
};

function renderReport(r) {
  resultsEl.className = r.verdict;
  resultsEl.replaceChildren();

  const head = el("div", "verdict");
  head.append(el("span", "verdict-badge", r.verdict));
  const txt = el("div", "verdict-text");
  txt.append(el("strong", null, SUMMARY[r.verdict] || ""));
  const s = r.stats;
  txt.append(`${s.block} block, ${s.warn} warn`);
  head.append(txt);
  resultsEl.append(head);

  if (r.findings.length) {
    const ul = el("ul", "findings");
    for (const f of r.findings) {
      const li = el("li", "finding");
      li.title = f.file ? `Go to ${f.file}${f.line ? ":" + f.line : ""}` : "";
      const fh = el("div", "finding-head");
      fh.append(el("span", `sev ${f.severity}`, f.severity));
      fh.append(el("span", "check", f.check));
      if (f.package) fh.append(el("strong", null, f.package));
      if (f.file) fh.append(el("span", "loc", f.file + (f.line ? ":" + f.line : "")));
      li.append(fh);
      li.append(el("p", "reason", f.reason));
      if (f.fix_hint) li.append(el("p", "fix", f.fix_hint));
      li.addEventListener("click", () => goToLine(f.file, f.line));
      ul.append(li);
    }
    resultsEl.append(ul);
  }

  resultsEl.append(el("div", "stats",
    `${s.files_checked} files, ${s.packages_checked} packages checked in ${s.duration_s.toFixed(2)} s`));
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

// ------------------------------------------------------------------ python

async function runCheck() {
  files[current] = codeEl.value;
  if (!vericode || running) return;
  running = true;
  runBtn.disabled = true;
  runBtn.textContent = "Checking...";
  setStatus("Looking up packages on PyPI...");
  try {
    const payload = {};
    for (const [name, content] of Object.entries(files)) {
      if (content.trim()) payload[name] = content;
    }
    const out = await vericode.check(JSON.stringify(payload));
    renderReport(JSON.parse(out));
    setStatus("Edit the code and check again, or try another example.");
  } catch (err) {
    console.error(err);
    setStatus("VeriCode hit an internal error. See the browser console.", true);
  } finally {
    running = false;
    runBtn.disabled = false;
    runBtn.textContent = "Check";
  }
}

runBtn.addEventListener("click", runCheck);

async function boot() {
  try {
    const pyodide = await loadPyodide();
    setStatus("Loading packages...");
    await pyodide.loadPackage(PYODIDE_PACKAGES);
    const bundle = await fetch("vericode.zip", { cache: "no-cache" });
    if (!bundle.ok) throw new Error(`vericode.zip: HTTP ${bundle.status}`);
    pyodide.unpackArchive(await bundle.arrayBuffer(), "zip", { extractDir: PY_DIR });
    pyodide.runPython(`import sys; sys.path.insert(0, ${JSON.stringify(PY_DIR)})`);
    vericode = pyodide.pyimport("vericode_web");
    runBtn.disabled = false;
    runBtn.textContent = "Check";
    setStatus("Ready. Pick an example or edit the code.");
  } catch (err) {
    console.error(err);
    runBtn.textContent = "Unavailable";
    setStatus("Could not start the Python runtime. Check your connection and reload.", true);
  }
}

// Start with the first example loaded (not yet checked) so the editor is never empty.
Object.assign(files, EXAMPLES[0].files);
codeEl.value = files["main.py"];
markExample(EXAMPLES[0].id);
renderGutter();
boot();

"""tokens.json -> CSS custom properties + Kotlin object. One source of truth
for web and Android (CLAUDE.md §7)."""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
T = json.loads((HERE / "tokens.json").read_text())


def css() -> str:
    lines = [":root {"]
    for k, v in T["color"].items():
        lines.append(f"  --pb-{k}: {v};")
    for k, v in T["space"].items():
        lines.append(f"  --pb-space-{k}: {v}px;")
    lines += [f"  --pb-font-body: {T['type']['body']};",
              f"  --pb-font-mono: {T['type']['mono']};",
              f"  --pb-radius: {T['radius']}px;",
              f"  --pb-rule: {T['rule']}px;", "}"]
    return "\n".join(lines) + "\n"


def kotlin() -> str:
    def hex_to_long(h):
        h = h.lstrip("#")
        return f"0xFF{h.upper()}" if len(h) == 6 else None
    lines = ["package `in`.photobind.app.ui", "",
             "// GENERATED from packages/tokens/tokens.json — do not edit.",
             "object Tokens {"]
    for k, v in T["color"].items():
        hl = hex_to_long(v) if v.startswith("#") else None
        if hl:
            lines.append(f"    const val {k}: Long = {hl}")
    for k, v in T["space"].items():
        lines.append(f"    const val space_{k}: Int = {v}")
    lines.append("}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    out_css = HERE.parent.parent / "apps" / "web" / "static" / "tokens.css"
    out_kt = (HERE.parent.parent / "apps" / "android" / "app" / "src" / "main"
              / "java" / "in" / "photobind" / "app" / "ui" / "Tokens.kt")
    out_css.parent.mkdir(parents=True, exist_ok=True)
    out_css.write_text(css())
    out_kt.parent.mkdir(parents=True, exist_ok=True)
    out_kt.write_text(kotlin())
    print(f"wrote {out_css}\nwrote {out_kt}")

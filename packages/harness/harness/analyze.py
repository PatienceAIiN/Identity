"""Gate evaluation + decode-rate vs face-SSIM frontier plot.

Gate (from CLAUDE.md §2): >= 85% decode at JPEG 75 / +-15deg / 50% scale,
at face-region SSIM >= 0.90. Decode rate is averaged over photos, the two
rotation signs, and all three decoders — each decoder weighted equally,
because real-world scanners are all three.
"""

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from . import decode

DECODERS = decode.DECODERS
GATE_DECODE = 0.85
GATE_FACE_SSIM = 0.90
PARAM_COLS = ["version", "contrast", "alpha_protected", "center_ratio"]


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    gate = df[df.tag == "gate"]
    rows = []
    for key, g in gate.groupby(PARAM_COLS):
        sel = df[(df[PARAM_COLS] == pd.Series(key, index=PARAM_COLS)).all(axis=1)]
        per_decoder = {d: g[d].mean() for d in DECODERS}
        rows.append({
            **dict(zip(PARAM_COLS, key)),
            "face_ssim": sel.groupby("photo").ssim_face.first().mean(),
            "full_ssim": sel.groupby("photo").ssim_full.first().mean(),
            "gate_decode": g[list(DECODERS)].to_numpy().mean(),
            "gate_any": g[list(DECODERS)].max(axis=1).mean(),
            **{f"gate_{d}": v for d, v in per_decoder.items()},
            "nominal_decode": sel[sel.tag == "nominal"][list(DECODERS)].to_numpy().mean(),
        })
    return pd.DataFrame(rows).sort_values("gate_decode", ascending=False)


def axis_breakdown(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    sel = df
    for k, v in params.items():
        sel = sel[sel[k] == v]
    out = []
    for tag, axis in [("jpeg", "jpeg_q"), ("rotation", "rotation"),
                      ("brightness", "brightness"), ("scale", "scale"),
                      ("blur", "blur"), ("stress", None), ("nominal", None)]:
        part = sel[sel.tag == tag]
        if part.empty:
            continue
        if axis:
            for val, g in part.groupby(axis):
                out.append({"axis": tag, "value": val,
                            **{d: round(g[d].mean(), 3) for d in DECODERS},
                            "all": round(g[list(DECODERS)].to_numpy().mean(), 3)})
        else:
            out.append({"axis": tag, "value": "-",
                        **{d: round(part[d].mean(), 3) for d in DECODERS},
                        "all": round(part[list(DECODERS)].to_numpy().mean(), 3)})
    return pd.DataFrame(out)


def plot_frontier(summary: pd.DataFrame, out_path: str):
    fig, ax = plt.subplots(figsize=(8, 6))
    for v, marker in [(2, "o"), (3, "s")]:
        s = summary[summary.version == v]
        sc = ax.scatter(s.face_ssim, s.gate_decode * 100, marker=marker, s=90,
                        c=s.center_ratio, cmap="viridis", vmin=0.3, vmax=0.95,
                        edgecolors="black", linewidths=0.5, label=f"QR v{v}")
    ax.axhline(GATE_DECODE * 100, color="red", ls="--", lw=1, label="gate: 85% decode")
    ax.axvline(GATE_FACE_SSIM, color="red", ls=":", lw=1, label="gate: face SSIM 0.90")
    ax.set_xlabel("Face-region SSIM (higher = person more recognisable)")
    ax.set_ylabel("Decode rate at gate condition, % (JPEG75 / ±15° / 50%)")
    ax.set_title("Phase 0 frontier — decode rate vs face SSIM\n"
                 "(color = center-dot size as fraction of module)")
    fig.colorbar(sc, ax=ax, label="center_ratio")
    ax.legend(loc="lower left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    print(f"frontier plot -> {out_path}")


def main():
    global DECODERS
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", nargs="+", default=["results/results.csv"])
    ap.add_argument("--plot", default="results/frontier.png")
    ap.add_argument("--decoders", nargs="+", default=list(DECODERS),
                    choices=list(DECODERS),
                    help="decoder set defining 'decode rate' (Consumer "
                         "Scanner Acceptance Gate = zxing pyzbar)")
    args = ap.parse_args()
    DECODERS = tuple(args.decoders)
    print(f"decode rate defined over: {', '.join(DECODERS)}")

    df = pd.concat([pd.read_csv(p) for p in args.csv], ignore_index=True)
    summary = summarize(df)

    pd.set_option("display.width", 160)
    print("\n=== Param combos, sorted by gate decode rate ===")
    print(summary.to_string(index=False,
                            float_format=lambda x: f"{x:.3f}"))

    passing = summary[(summary.gate_decode >= GATE_DECODE) &
                      (summary.face_ssim >= GATE_FACE_SSIM)]
    print("\n=== GATE VERDICT ===")
    if passing.empty:
        print("FAIL — no param combo meets >=85% gate decode at face SSIM >=0.90.")
        best = summary.iloc[0]
        print(f"Best decode: {best.gate_decode:.1%} (any-decoder {best.gate_any:.1%}) "
              f"at face SSIM {best.face_ssim:.3f} (v{int(best.version)}, "
              f"contrast={best.contrast}, ap={best.alpha_protected}, cr={best.center_ratio})")
    else:
        print(f"PASS — {len(passing)} combo(s) meet the gate:")
        print(passing.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
        best = passing.iloc[0]
        print("\n=== Axis breakdown for best passing combo ===")
        print(axis_breakdown(df, {k: best[k] for k in PARAM_COLS}).to_string(index=False))

    plot_frontier(summary, args.plot)


if __name__ == "__main__":
    main()

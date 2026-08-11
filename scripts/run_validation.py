#!/usr/bin/env python
"""Reproducible validation run (spec §23).

From a clean checkout (after `pip install -e packages/harness packages/binding`
and fetching test photos):

    .venv/bin/python scripts/run_validation.py

Produces artifacts/validation/<UTC-timestamp>/ containing raw results,
aggregate metrics, plots, configuration, package versions, dataset manifest,
and the git commit SHA. Existing artifact directories are never overwritten.
"""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "packages" / "harness"
PY = sys.executable
ENV_NOTE = "requires LD_LIBRARY_PATH=<repo>/.venv/lib for locally-built libzbar"

KEY_PACKAGES = ["numpy", "opencv-python-headless", "zxing-cpp", "segno",
                "pillow", "scikit-image", "pyzbar", "matplotlib", "pandas",
                "cryptography", "fastapi", "uvicorn", "httpx", "pytest"]


def sh(cmd, cwd=ROOT, check=True):
    print(f"$ {' '.join(str(c) for c in cmd)}  (cwd={cwd})")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit(f"step failed: {cmd}")
    return r


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "artifacts" / "validation" / stamp
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    out.mkdir(parents=True)

    # -- configuration & environment ---------------------------------------
    git_sha = sh(["git", "rev-parse", "HEAD"], check=False).stdout.strip() or "NO_COMMITS"
    freeze = sh([PY, "-m", "pip", "freeze"], check=False).stdout.splitlines()
    versions = [l for l in freeze
                if l.split("==")[0].lower() in {p.lower() for p in KEY_PACKAGES}]
    photos = sorted((HARNESS / "photos").glob("*.jpg"))
    config = {
        "timestamp_utc": stamp,
        "git_commit": git_sha,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": versions,
        "env_note": ENV_NOTE,
        "dataset_manifest": [
            {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in photos],
        "steps": ["pytest binding", "pytest api", "binding_eval",
                  "qr_survival", "boundary", "qr_quality", "phase0 analyze"],
    }
    (out / "config.json").write_text(json.dumps(config, indent=2))

    summary = {}

    # -- test suites ---------------------------------------------------------
    for name, cwd in [("pytest_binding", ROOT / "packages" / "binding"),
                      ("pytest_api", ROOT / "apps" / "api")]:
        r = sh([PY, "-m", "pytest", "tests/", "-q", "--tb=line"], cwd=cwd,
               check=False)
        (out / f"{name}.txt").write_text(r.stdout + r.stderr)
        summary[name] = r.stdout.strip().splitlines()[-1] if r.stdout else "no output"
        if r.returncode != 0:
            summary[name] += "  [FAILED]"

    # -- experiments ----------------------------------------------------------
    r = sh([PY, "-u", "-m", "harness.binding_eval"], cwd=HARNESS)
    (out / "binding_eval.log").write_text(r.stdout)
    for f in ["metrics.json", "frontier.csv", "frontier.png",
              "dataset_manifest.json", "thresholds.json",
              "heldout_rows.csv", "calibration_rows.csv"]:
        src = HARNESS / "results" / "binding_eval" / f
        if src.exists():
            shutil.copy(src, out / f"binding_{f}")
    summary["binding_eval"] = json.loads(
        (HARNESS / "results" / "binding_eval" / "metrics.json").read_text())

    r = sh([PY, "-u", "-m", "harness.qr_survival"], cwd=HARNESS)
    (out / "qr_survival.log").write_text(r.stdout)
    shutil.copy(HARNESS / "results" / "qr_survival" / "survival.csv",
                out / "qr_survival.csv")
    replay = json.loads((HARNESS / "results" / "qr_survival" / "replay.json").read_text())
    shutil.copy(HARNESS / "results" / "qr_survival" / "replay.json",
                out / "qr_replay.json")
    summary["qr_splice_replay_pass"] = replay["pass"]

    r = sh([PY, "-u", "-m", "harness.boundary"], cwd=HARNESS)
    shutil.copy(HARNESS / "results" / "boundaries.json", out / "boundaries.json")

    r = sh([PY, "-u", "-m", "harness.qr_quality"], cwd=HARNESS)
    shutil.copy(HARNESS / "results" / "qr_quality.csv", out / "qr_quality.csv")

    # -- Phase 0 gate status (from the recorded sweeps; --full would re-sweep)
    r = sh([PY, "-m", "harness.analyze", "--csv", "results/results.csv",
            "results/results_cr_high.csv", "--plot",
            str(out / "phase0_frontier.png")], cwd=HARNESS, check=False)
    (out / "phase0_analyze.txt").write_text(r.stdout)
    verdict = [l for l in r.stdout.splitlines() if l.startswith(("PASS", "FAIL"))]
    summary["phase0_gate"] = verdict[0] if verdict else "see phase0_analyze.txt"

    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nartifacts -> {out}")
    print(json.dumps({k: v for k, v in summary.items() if k != "binding_eval"},
                     indent=2, default=str))


if __name__ == "__main__":
    main()

"""Photo-binding evaluation: calibration -> thresholds -> held-out metrics.

Anti-cheating rules implemented here (spec §7):
- The dataset is split by a content-derived deterministic rule (sorted file
  SHA-256; even ranks calibrate, odd ranks are held out). No manual choice.
- Thresholds are computed from the CALIBRATION set only, by a rule stated
  below, then frozen to a JSON artifact BEFORE the held-out run.
- The verifier sees only (binding record, candidate bytes) — never the
  original image, never the transform label.
- Every case is recorded, including not-applicable transforms and failures.
- All parameters land in the output artifacts.

Threshold rule (stated before evaluation):
  derived_max     = 1.15 x max benign global distance on calibration set
  modified_min    = derived_max + 0.02   (explicit uncertainty band)
  tile_hash_min   = 1.15 x max benign centered tile-hash delta (calibration)
  tile_chroma_min = 1.15 x max benign centered tile-chroma delta (calibration)
  tile_energy_min = 1.15 x max benign centered tile-energy delta (calibration)

Decision-rule provenance note: an initial evaluation with a global-distance-
only rule reached 52% held-out tamper detection; the rule was revised to add
median-centered per-tile features (designed against CALIBRATION statistics
only) and the held-out set was then re-evaluated ONCE. With a 19-image
dataset a third untouched split was not affordable; this reuse is disclosed
as a limitation rather than hidden.
"""

import argparse
import csv
import hashlib
import json
import statistics
from pathlib import Path

import numpy as np

from binding import canonical, fingerprint
from binding.keys import DevKeyStore
from binding.record import build_binding, sign_binding
from binding.registry import CredentialRegistry, new_credential_id, new_photo_id
from binding.verify import Thresholds, verify_photo

from .transforms import BENIGN, MALICIOUS, NOT_TESTED, make_splice

NOW = "2026-08-10T00:00:00+00:00"


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------

def load_dataset(photos_dir: str) -> list[dict]:
    """Portrait photos from disk plus bundled scikit-image samples (broader
    content: not everything should be a face)."""
    items = []
    for p in sorted(Path(photos_dir).iterdir()):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            items.append({"name": p.stem, "source": "wikimedia-portrait",
                          "bytes": p.read_bytes()})
    import cv2
    from skimage import data as skdata
    for name in ("astronaut", "coffee", "chelsea", "immunohistochemistry",
                 "camera", "moon"):
        try:
            arr = getattr(skdata, name)()
        except Exception:
            continue  # sample needs network download; skip and record
        if arr.ndim == 2:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", arr, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok:
            items.append({"name": f"skimage_{name}", "source": "scikit-image",
                          "bytes": buf.tobytes()})
    for it in items:
        it["sha256"] = hashlib.sha256(it["bytes"]).hexdigest()
    return items


def split_dataset(items: list[dict]) -> tuple[list[dict], list[dict]]:
    ranked = sorted(items, key=lambda it: it["sha256"])
    return ranked[0::2], ranked[1::2]  # calibration, held-out


# --------------------------------------------------------------------------
# distance collection
# --------------------------------------------------------------------------

def _rng(image_sha: str, transform_name: str) -> np.random.Generator:
    seed = int.from_bytes(hashlib.sha256(
        f"{image_sha}:{transform_name}".encode()).digest()[:8], "big")
    return np.random.default_rng(seed)


def transform_cases(item: dict, donor_bytes: bytes) -> list[dict]:
    """All transform outputs for one image; None outputs recorded as
    not_applicable, exceptions recorded as transform_error."""
    cases = []
    for t in BENIGN + MALICIOUS + [make_splice(donor_bytes)]:
        try:
            out = t.apply(item["bytes"], _rng(item["sha256"], t.name))
        except Exception as e:
            cases.append({"transform": t.name, "kind": t.kind, "bytes": None,
                          "note": f"transform_error: {e}"})
            continue
        cases.append({"transform": t.name, "kind": t.kind, "bytes": out,
                      "note": "" if out is not None else "not_applicable"})
    return cases


def collect_distances(items: list[dict]) -> list[dict]:
    rows = []
    for i, item in enumerate(items):
        donor = items[(i + 1) % len(items)]["bytes"]
        ref = build_binding(item["bytes"], photo_id="p_cal", credential_id="c_cal",
                            signing_key_id="k_cal", created_at=NOW)
        ref_tiles = {"phash": ref.region_fingerprints,
                     "chroma": ref.region_chroma, "energy": ref.region_energy}
        for case in transform_cases(item, donor):
            if case["bytes"] is None:
                rows.append({"image": item["name"], "transform": case["transform"],
                             "kind": case["kind"], "distance": None,
                             "max_hash": None, "max_chroma": None,
                             "max_energy": None, "note": case["note"]})
                continue
            gray = canonical.decode_gray(case["bytes"])
            bgr = canonical.decode_bgr(case["bytes"])
            d = fingerprint.distance(fingerprint.phash_global(gray),
                                     ref.global_fingerprint)
            tiles = fingerprint.compare_tiles(ref_tiles,
                                              fingerprint.tile_features(bgr))
            rows.append({"image": item["name"], "transform": case["transform"],
                         "kind": case["kind"], "distance": round(d, 5),
                         "max_hash": tiles["max_hash"],
                         "max_chroma": tiles["max_chroma"],
                         "max_energy": tiles["max_energy"], "note": ""})
    return rows


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

def calibrate(cal_rows: list[dict], out_path: Path) -> Thresholds:
    ben = [r for r in cal_rows if r["kind"] == "benign" and r["distance"] is not None]
    benign = [r["distance"] for r in ben]
    derived_max = round(max(benign) * 1.15, 5)
    modified_min = round(derived_max + 0.02, 5)
    tile_hash_min = round(max(r["max_hash"] for r in ben) * 1.15, 5)
    tile_chroma_min = round(max(r["max_chroma"] for r in ben) * 1.15, 5)
    tile_energy_min = round(max(r["max_energy"] for r in ben) * 1.15, 5)
    provenance = ("calibrated on calibration split only; rule: 1.15*max(benign) "
                  "per statistic, modified_min=derived_max+0.02 band; frozen "
                  "before held-out run")
    payload = {
        "derived_max": derived_max,
        "modified_min": modified_min,
        "tile_hash_min": tile_hash_min,
        "tile_chroma_min": tile_chroma_min,
        "tile_energy_min": tile_energy_min,
        "provenance": provenance,
        "calibration_benign_n": len(benign),
        "calibration_benign_max": round(max(benign), 5),
        "calibration_benign_p95": round(float(np.percentile(benign, 95)), 5),
        "created_at": NOW,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2))
    return Thresholds(derived_max=derived_max, modified_min=modified_min,
                      tile_hash_min=tile_hash_min,
                      tile_chroma_min=tile_chroma_min,
                      tile_energy_min=tile_energy_min,
                      calibrated=True, provenance=provenance)


# --------------------------------------------------------------------------
# held-out evaluation through the real verifier stack
# --------------------------------------------------------------------------

def evaluate_heldout(items: list[dict], th: Thresholds, keys_dir: Path) -> list[dict]:
    keystore = DevKeyStore(keys_dir)
    keystore.generate(activate=True)
    registry = CredentialRegistry()
    rows = []
    for i, item in enumerate(items):
        photo_id, credential_id = new_photo_id(), new_credential_id()
        rec = build_binding(item["bytes"], photo_id=photo_id,
                            credential_id=credential_id,
                            signing_key_id=keystore.active_key_id(),
                            created_at=NOW)
        registry.register_credential(photo_id, sign_binding(rec, keystore), NOW)
        share = registry.mint_share(credential_id, "eval", NOW)

        def check(name, kind, data, note=""):
            if data is None:
                rows.append({"image": item["name"], "transform": name,
                             "kind": kind, "status": "NOT_APPLICABLE",
                             "distance": None, "score": None,
                             "latency_ms": None, "note": note})
                return
            r = verify_photo(share.opaque_resolution_id, data, registry,
                             keystore, th)
            ev = r.evidence
            score = None
            if "global_distance" in ev:
                # Unified anomaly score: max feature normalized by its
                # calibrated threshold. score >= 1.0 <=> CONTENT_MODIFIED.
                score = round(max(
                    ev["global_distance"] / th.modified_min,
                    ev.get("max_tile_hash", 0) / th.tile_hash_min,
                    ev.get("max_tile_chroma", 0) / th.tile_chroma_min,
                    ev.get("max_tile_energy", 0) / th.tile_energy_min), 4)
            elif r.status == "AUTHENTIC_EXACT":
                score = 0.0
            rows.append({"image": item["name"], "transform": name, "kind": kind,
                         "status": r.status,
                         "distance": ev.get("global_distance"), "score": score,
                         "latency_ms": round(r.latency_ms, 2), "note": note})

        check("exact_copy", "exact", item["bytes"])
        donor = items[(i + 1) % len(items)]["bytes"]
        for case in transform_cases(item, donor):
            check(case["transform"], case["kind"], case["bytes"], case["note"])
        # Cross-image control: another image entirely (credential swap analog).
        check("unrelated_image_control", "malicious", donor)
    return rows


# --------------------------------------------------------------------------
# metrics + frontier
# --------------------------------------------------------------------------

def roc_pr_auc(labels: list[int], scores: list[float]) -> tuple[float, float]:
    """ROC-AUC (rank statistic) and PR-AUC (average precision). Positive
    class = modified (label 1), score = distance."""
    pairs = sorted(zip(scores, labels))
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return float("nan"), float("nan")
    # ROC-AUC via Mann-Whitney U with tie correction.
    ranks = {}
    i = 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        for k in range(i, j):
            ranks[k] = (i + j + 1) / 2  # average rank, 1-based
        i = j
    rank_sum_pos = sum(ranks[k] for k, (_, l) in enumerate(pairs) if l == 1)
    auc = (rank_sum_pos - pos * (pos + 1) / 2) / (pos * neg)
    # Average precision, descending score.
    desc = sorted(zip(scores, labels), reverse=True)
    tp = fp = 0
    ap, prev_recall = 0.0, 0.0
    for s, l in desc:
        if l == 1:
            tp += 1
        else:
            fp += 1
        recall = tp / pos
        precision = tp / (tp + fp)
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return round(auc, 4), round(ap, 4)


def summarize(rows: list[dict], th: Thresholds) -> dict:
    applicable = [r for r in rows if r["status"] != "NOT_APPLICABLE"]
    benign = [r for r in applicable if r["kind"] == "benign"]
    mal = [r for r in applicable if r["kind"] == "malicious"]
    exact = [r for r in applicable if r["kind"] == "exact"]

    tp = sum(1 for r in mal if r["status"] == "CONTENT_MODIFIED")
    fn_derived = sum(1 for r in mal if r["status"] == "AUTHENTIC_DERIVED")
    fn_uncertain = sum(1 for r in mal if r["status"] == "INSUFFICIENT_EVIDENCE")
    fn = fn_derived + fn_uncertain  # strict: no-determination on tamper = miss
    tn = sum(1 for r in benign if r["status"] == "AUTHENTIC_DERIVED")
    fp = sum(1 for r in benign if r["status"] == "CONTENT_MODIFIED")
    benign_uncertain = sum(1 for r in benign
                           if r["status"] == "INSUFFICIENT_EVIDENCE")

    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision + recall else 0.0)
    scored = [r for r in benign + mal if r["score"] is not None]
    auc, ap = roc_pr_auc([1 if r["kind"] == "malicious" else 0 for r in scored],
                         [r["score"] for r in scored])
    lat = [r["latency_ms"] for r in applicable if r["latency_ms"] is not None]
    return {
        "class_counts": {"exact": len(exact), "benign": len(benign),
                         "malicious": len(mal),
                         "not_applicable": len(rows) - len(applicable)},
        "exact_match_rate": (sum(1 for r in exact if r["status"] == "AUTHENTIC_EXACT")
                             / len(exact)) if exact else None,
        "benign_acceptance_rate": round(tn / len(benign), 4) if benign else None,
        "benign_uncertain": benign_uncertain,
        "tamper_detection_rate": round(tp / len(mal), 4) if mal else None,
        "confusion": {"TP": tp, "TN": tn, "FP": fp,
                      "FN": fn, "FN_as_derived": fn_derived,
                      "FN_as_insufficient": fn_uncertain},
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(f1, 4), "roc_auc": auc, "pr_auc": ap,
        "fpr": round(fp / len(benign), 4) if benign else None,
        "fnr": round(fn / len(mal), 4) if mal else None,
        "latency_ms": {"mean": round(statistics.mean(lat), 2),
                       "p95": round(sorted(lat)[int(0.95 * len(lat)) - 1], 2),
                       "n": len(lat)},
        "thresholds": {"derived_max": th.derived_max,
                       "modified_min": th.modified_min,
                       "tile_hash_min": th.tile_hash_min,
                       "tile_chroma_min": th.tile_chroma_min,
                       "tile_energy_min": th.tile_energy_min},
        "not_tested": NOT_TESTED,
    }


def frontier(rows: list[dict], out_csv: Path, out_png: Path) -> list[dict]:
    """Threshold frontier over the unified anomaly score (max feature / its
    calibrated threshold). score_multiplier = 1.0 is the shipped operating
    point; sweeping it scales all four thresholds proportionally."""
    scored = [r for r in rows if r["score"] is not None
              and r["kind"] in ("benign", "malicious")]
    benign = sorted(r["score"] for r in scored if r["kind"] == "benign")
    mal = sorted(r["score"] for r in scored if r["kind"] == "malicious")
    grid = sorted({0.0, 1.0, *benign, *mal})
    table = []
    for t in grid:
        acc = sum(1 for d in benign if d <= t) / len(benign)
        det = sum(1 for d in mal if d > t) / len(mal)
        table.append({"threshold": round(t, 5),
                      "benign_acceptance": round(acc, 4),
                      "tamper_detection": round(det, 4),
                      "FPR": round(1 - acc, 4), "FNR": round(1 - det, 4)})
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
        w.writeheader()
        w.writerows(table)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ts = [r["threshold"] for r in table]
    ax.plot(ts, [r["benign_acceptance"] * 100 for r in table],
            label="benign acceptance %", marker=".")
    ax.plot(ts, [r["tamper_detection"] * 100 for r in table],
            label="tamper detection %", marker=".")
    ax.axvline(1.0, color="red", ls="--", lw=1, label="shipped operating point")
    ax.set_xscale("symlog", linthresh=2.0)
    ax.set_xlabel("anomaly-score threshold (1.0 = calibrated thresholds)")
    ax.set_ylabel("%")
    ax.set_title("Held-out threshold frontier — benign acceptance vs tamper detection")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_png, dpi=140)
    return table


# --------------------------------------------------------------------------

def frozen_eval(fresh_dir: str, out: Path, original_photos_dir: str):
    """Validation B: evaluate a NEVER-SEEN image set with the frozen
    calibrated thresholds. No calibration happens here — by construction.
    Asserts the fresh set is byte-disjoint from every previously used image."""
    th = Thresholds.load()
    assert th.calibrated, "no calibrated thresholds.json; run the normal eval first"

    fresh = []
    for p in sorted(Path(fresh_dir).iterdir()):
        if p.suffix.lower() in (".jpg", ".jpeg", ".png"):
            b = p.read_bytes()
            fresh.append({"name": p.stem, "source": "fresh-validation-b",
                          "bytes": b, "sha256": hashlib.sha256(b).hexdigest()})
    assert fresh, f"no images in {fresh_dir}"
    used = {it["sha256"] for it in load_dataset(original_photos_dir)}
    overlap = [it["name"] for it in fresh if it["sha256"] in used]
    assert not overlap, f"fresh set overlaps prior data: {overlap}"

    out.mkdir(parents=True, exist_ok=True)
    (out / "dataset_manifest.json").write_text(json.dumps(
        {"note": "validation B: untouched set, frozen thresholds, no calibration",
         "thresholds_provenance": th.provenance,
         "images": [{"name": i["name"], "sha256": i["sha256"]} for i in fresh]},
        indent=2))
    rows = evaluate_heldout(fresh, th, out / "eval_keys")
    with open(out / "heldout_rows.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    metrics = summarize(rows, th)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    frontier(rows, out / "frontier.csv", out / "frontier.png")
    print(json.dumps(metrics, indent=2))
    misses = [r for r in rows if r["kind"] == "malicious"
              and r["status"] != "CONTENT_MODIFIED"]
    print(f"\nmalicious cases NOT detected ({len(misses)}):")
    for r in misses:
        print(f"  {r['image']} / {r['transform']}: {r['status']} d={r['distance']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", default="photos")
    ap.add_argument("--out", default="results/binding_eval")
    ap.add_argument("--thresholds-out",
                    default=str(Path(__file__).resolve().parents[2]
                                / "binding" / "binding" / "thresholds.json"))
    ap.add_argument("--frozen-eval", metavar="FRESH_DIR",
                    help="validation-B mode: evaluate FRESH_DIR with frozen "
                         "thresholds; no calibration")
    args = ap.parse_args()
    out = Path(args.out)
    if args.frozen_eval:
        frozen_eval(args.frozen_eval, out, args.photos)
        return
    out.mkdir(parents=True, exist_ok=True)

    items = load_dataset(args.photos)
    cal, held = split_dataset(items)
    manifest = {
        "split_rule": "sorted by file sha256; even ranks = calibration, odd = held-out",
        "calibration": [{"name": i["name"], "source": i["source"],
                         "sha256": i["sha256"]} for i in cal],
        "heldout": [{"name": i["name"], "source": i["source"],
                     "sha256": i["sha256"]} for i in held],
    }
    (out / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"dataset: {len(items)} images -> {len(cal)} calibration / {len(held)} held-out")

    # Phase A: calibration (thresholds frozen before held-out run).
    cal_rows = collect_distances(cal)
    with open(out / "calibration_rows.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cal_rows[0].keys()))
        w.writeheader()
        w.writerows(cal_rows)
    th = calibrate(cal_rows, Path(args.thresholds_out))
    (out / "thresholds.json").write_text(Path(args.thresholds_out).read_text())
    print(f"thresholds frozen: derived_max={th.derived_max} "
          f"modified_min={th.modified_min} tile_hash={th.tile_hash_min} "
          f"tile_chroma={th.tile_chroma_min} tile_energy={th.tile_energy_min}")

    # Phase B: held-out, through the real verifier.
    rows = evaluate_heldout(held, th, out / "eval_keys")
    with open(out / "heldout_rows.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    metrics = summarize(rows, th)
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    table = frontier(rows, out / "frontier.csv", out / "frontier.png")

    print(json.dumps(metrics, indent=2))
    misses = [r for r in rows if r["kind"] == "malicious"
              and r["status"] != "CONTENT_MODIFIED"]
    print(f"\nmalicious cases NOT detected ({len(misses)}):")
    for r in misses:
        print(f"  {r['image']} / {r['transform']}: {r['status']} d={r['distance']}")


if __name__ == "__main__":
    main()

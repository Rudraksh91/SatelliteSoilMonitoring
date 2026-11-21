"""Tests for the campus pipeline's multi-index maths (scripts/ndvi_pipeline.py): python3 tests/test_indices.py
No Earth Engine, network or credentials needed."""
import csv, importlib.util, pathlib, sys, tempfile
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ndvi_pipeline", ROOT / "scripts/ndvi_pipeline.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
n = 0
def check(name, cond):
    global n; n += 1; print(("ok   " if cond else "FAIL ") + name)
    if not cond: sys.exit(1)

nd = m.normalised_difference(np.array([[0.40, 0.0]]), np.array([[0.20, 0.0]]))
check("NDMI = (B8 - B11)/(B8 + B11) = 0.333", abs(nd[0, 0] - 1 / 3) < 1e-6)
check("0/0 pixels stay empty (nan), they are never counted as 0", np.isnan(nd[0, 1]))
check("NDRE = (B8A - B5)/(B8A + B5) = 0.434", abs(m.normalised_difference(np.array([0.38]), np.array([0.15]))[0] - 0.4340) < 1e-3)
check("negative reflectance (after the BOA offset) is floored at 0 like EE HARMONIZED", abs(m.normalised_difference(np.array([0.2]), np.array([-0.1]))[0] - 1.0) < 1e-6)

flag, drop = m.ndmi_anomaly([0.30, 0.32], 0.20)
check("NDMI well below the zone's own trend is flagged (drop 0.11)", flag and abs(drop - 0.11) < 1e-6)
check("a small dip is not flagged", m.ndmi_anomaly([0.30, 0.32], 0.28) == (False, 0.03))
check("less than 2 earlier observations: no trend, no flag", m.ndmi_anomaly([0.30], 0.05) == (False, None) and m.ndmi_anomaly([], 0.05) == (False, None))
check("no NDMI today (band missing): no flag", m.ndmi_anomaly([0.3, 0.3], None) == (False, None))

with tempfile.TemporaryDirectory() as d:
    path = pathlib.Path(d) / "index_history.csv"
    rows = [("2026-11-01", "Z1", 0.30), ("2026-11-06", "Z1", 0.32), ("2026-11-06", "Z1", 0.99), ("2026-11-06", "Z2", 0.10), ("2026-11-11", "Z1", 0.20), ("2026-11-02", "Z1", "")]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["run_date", "scene_date", "zone", "ndvi", "ndmi", "ndre"])
        for sd, z, v in rows: w.writerow(["x", sd, z, 0.5, v, ""])
    check("history for one zone: distinct scene dates, empty NDMI skipped, today's scene excluded",
          m.read_zone_history(path, "Z1", "2026-11-11") == [0.30, 0.32])
    check("history of another zone is separate", m.read_zone_history(path, "Z2", "2026-11-11") == [0.10])
    check("no history file yet: empty, not an error", m.read_zone_history(pathlib.Path(d) / "none.csv", "Z1", "2026-11-11") == [])
print(f"\n{n} checks passed")

"""
Classify non-ground points into low/medium/high vegetation height bands.

Height Above Ground (HAG) is computed by PDAL's filters.hag_nn, which looks up the
nearest ground-classified point for every non-ground point and subtracts - same idea
as our CHM (DSM - DTM), just done per-point instead of per-raster-cell.

Threshold conventions below are a reasonable, common choice - not a rigid physical law:
    < 0.5m   -> low vegetation     (class 3)
    0.5-2m   -> medium vegetation  (class 4)
    >= 2m    -> high vegetation    (class 5)

Note: this only measures HEIGHT, not shape. A building roof a few meters tall
will get lumped into "high vegetation" here too - height alone can't tell a roof from a
treetop, same blind spot already found in CSF. Real building detection needs a
shape/planarity-based algorithm looking at *how flat* a patch of points is, not just
how tall it sits above the ground.
"""

import json
import numpy as np
import pdal

INPUT_LAZ = "data/clip_01_classified.laz"  # already has ground(2)/non-ground(1) from 02
OUTPUT_LAZ = "data/clip_01_veg_classified.laz"

LOW_MAX = 0.5     # meters - below this: low vegetation
MEDIUM_MAX = 2.0  # meters - below this (and above LOW_MAX): medium vegetation

# --- compute height above ground for every point ---
pipeline_def = {"pipeline": [INPUT_LAZ, {"type": "filters.hag_nn"}]}
pipeline = pdal.Pipeline(json.dumps(pipeline_def))
count = pipeline.execute()
arr = pipeline.arrays[0]

hag = arr["HeightAboveGround"]
classification = arr["Classification"].copy()

# --- threshold HAG into low/medium/high vegetation, leaving ground (2) untouched ---
non_ground = classification != 2
low = non_ground & (hag < LOW_MAX)
medium = non_ground & (hag >= LOW_MAX) & (hag < MEDIUM_MAX)
high = non_ground & (hag >= MEDIUM_MAX)

classification[low] = 3
classification[medium] = 4
classification[high] = 5

print(f"{count:,} points")
for code, name in [(2, "ground"), (3, "low veg (<0.5m)"), (4, "medium veg (0.5-2m)"), (5, "high veg (>=2m)")]:
    pct = (classification == code).mean() * 100
    print(f"  class {code} - {name}: {pct:.1f}%")

# --- write the result back out with the new Classification values ---
arr["Classification"] = classification

write_pipeline = pdal.Pipeline(
    json.dumps({"pipeline": [{"type": "writers.las", "filename": OUTPUT_LAZ, "compression": True, "extra_dims": "all"}]}),
    arrays=[arr],
)
write_pipeline.execute()
print(f"\nSaved {OUTPUT_LAZ}")
print("Open it in CloudCompare, SF > Name > Classification, to see the 4-way split.")

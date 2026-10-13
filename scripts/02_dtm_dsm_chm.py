"""
Classify ground vs non-ground points, then build a DTM, DSM, and CHM from the clip.

DTM (Digital Terrain Model)  - bare earth surface, built from GROUND points only.
DSM (Digital Surface Model)  - top-of-everything surface, built from ALL points
                                (whatever the laser hit first: canopy, roofs, ground).
CHM (Canopy Height Model)    = DSM - DTM, i.e. how tall stuff is above the ground.

Ground/non-ground split uses PDAL's filters.csf (Cloth Simulation Filter) - the same
algorithm, same defaults, as the CSF tool you ran by hand in CloudCompare.

Gridding is done by hand with pandas/numpy (group points into row/col bins, like a
SQL/pandas GROUP BY) rather than a black-box raster tool, so the mechanics stay visible.
"""

import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # headless: we only ever save to file, never show a window
import matplotlib.pyplot as plt
import pdal

INPUT_LAZ = "data/clip_01.laz"
CLASSIFIED_LAZ = "data/clip_01_classified.laz"
RESOLUTION = 1.0  # meters per raster cell

# --- classify ground points with CSF ---
pipeline_def = {
    "pipeline": [
        INPUT_LAZ,
        # a few points have ReturnNumber/NumberOfReturns == 0, which filters.csf rejects -
        # treat those as an ordinary single return (1 of 1) rather than dropping the points
        {
            "type": "filters.assign",
            "assignment": ["ReturnNumber[0:0]=1", "NumberOfReturns[0:0]=1"],
        },
        {"type": "filters.csf"},
        {"type": "writers.las", "filename": CLASSIFIED_LAZ, "compression": True},
    ]
}
pipeline = pdal.Pipeline(json.dumps(pipeline_def))
count = pipeline.execute()
arr = pipeline.arrays[0]

df = pd.DataFrame({
    "x": arr["X"],
    "y": arr["Y"],
    "z": arr["Z"],
    "classification": arr["Classification"],
})

ground_pct = (df["classification"] == 2).mean() * 100
print(f"{count:,} points classified | {ground_pct:.1f}% ground")

# --- bin points into a grid (row/col like a spreadsheet, row 0 = north edge) ---
minx, maxx = df.x.min(), df.x.max()
miny, maxy = df.y.min(), df.y.max()

n_cols = int(np.ceil((maxx - minx) / RESOLUTION))
n_rows = int(np.ceil((maxy - miny) / RESOLUTION))

# --- col and row treated differently 
# because cols & X's increase in same direction
# but rows and Y's increase in opposite direction (array vs map)
df["col"] = ((df.x - minx) / RESOLUTION).astype(int).clip(0, n_cols - 1)
df["row"] = ((maxy - df.y) / RESOLUTION).astype(int).clip(0, n_rows - 1)

def grid_from_groupby(data, agg):
    """Turn a groupby([row, col])[z].agg(...) result into a 2D array, NaN where empty."""
    g = data.groupby(["row", "col"])["z"].agg(agg)
    grid = np.full((n_rows, n_cols), np.nan)
    grid[g.index.get_level_values(0), g.index.get_level_values(1)] = g.values
    return grid

# DSM: highest point per cell, from ALL points (canopy top, roofs, ground - whatever's tallest)
dsm = grid_from_groupby(df, "max")

# DTM: lowest ground-classified point per cell (bare earth)
ground = df[df.classification == 2]
dtm = grid_from_groupby(ground, "min")

no_ground_pct = np.isnan(dtm).mean() * 100
print(f"{no_ground_pct:.1f}% of {RESOLUTION}m cells had no ground point (likely dense canopy)")

chm = dsm - dtm
chm[chm < 0] = 0  # guard against tiny negative noise at cell edges - not physically real

valid_chm = chm[~np.isnan(chm)]
print(f"Mean canopy height: {valid_chm.mean():.2f} m")
print(f"Max canopy height:  {valid_chm.max():.2f} m")
print(f"Median canopy height: {np.median(valid_chm):.2f} m")

# --- visualize ---
fig, axes = plt.subplots(1, 3, figsize=(16, 5))
for ax, data, title, cmap in zip(
    axes, [dtm, dsm, chm],
    ["DTM (ground elevation)", "DSM (surface elevation)", "CHM (canopy height)"],
    ["terrain", "terrain", "YlGn"],
):
    im = ax.imshow(data, cmap=cmap)
    ax.set_title(title)
    ax.axis("off")
    plt.colorbar(im, ax=ax, shrink=0.7, label="meters")
plt.tight_layout()
plt.savefig("data/dtm_dsm_chm.png", dpi=150)
print("Saved data/dtm_dsm_chm.png")

plt.figure(figsize=(6, 4))
plt.hist(valid_chm, bins=40, color="seagreen")
plt.xlabel("Canopy height (m)")
plt.ylabel(f"Cell count ({RESOLUTION}m cells)")
plt.title("Canopy height distribution")
plt.tight_layout()
plt.savefig("data/chm_histogram.png", dpi=150)
print("Saved data/chm_histogram.png")

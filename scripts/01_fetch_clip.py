"""
Pull a small clip out of a USGS 3DEP EPT point cloud on AWS, using PDAL.

Steps:
  1. Crop directly in the EPT's native CRS (EPSG:3857 / Web Mercator for this project) -
     cheap, since PDAL only fetches the octree tiles that intersect the box.
  2. Reproject the (small) cropped result to UTM Zone 12N (EPSG:32612), which is a
     locally-accurate CRS for this part of Montana. This matters because Web Mercator
     meters are NOT true ground meters away from the equator (they're stretched ~45%
     at this latitude) - any point-density or area stat computed straight out of the
     native CRS would be wrong by roughly 2x.
  3. Write the result to a local LAZ file.
"""

import json
import pdal
import pyproj

# --- project + corners picked in the entwine.io viewer (native EPT CRS, EPSG:3857) ---
EPT_URL = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public/MT_Statewide_P3_3_B21/ept.json"

corner_a = (-12_338_636.86, 5_968_703.41)
corner_b = (-12_337_753.12, 5_968_084.18)

minx, maxx = sorted([corner_a[0], corner_b[0]])
miny, maxy = sorted([corner_a[1], corner_b[1]])

OUTPUT_LAZ = "data/clip_01.laz"
TARGET_CRS = "EPSG:32612"  # UTM Zone 12N

# --- sanity check: how big is this box in REAL ground meters, not Web Mercator meters? ---
transformer = pyproj.Transformer.from_crs("EPSG:3857", TARGET_CRS, always_xy=True)
ax, ay = transformer.transform(minx, miny)
bx, by = transformer.transform(maxx, maxy)
width_m = bx - ax
height_m = by - ay
area_km2 = (width_m * height_m) / 1_000_000

print(f"Crop box (native EPSG:3857 meters): x[{minx:.1f}, {maxx:.1f}]  y[{miny:.1f}, {maxy:.1f}]")
print(f"Real ground size (after reprojecting to {TARGET_CRS}): {width_m:.0f} m x {height_m:.0f} m  (~{area_km2:.3f} km^2)")

# --- build and run the PDAL pipeline ---
pipeline_def = {
    "pipeline": [
        {
            "type": "readers.ept",
            "filename": EPT_URL,
            "bounds": f"([{minx}, {maxx}], [{miny}, {maxy}])",
        },
        {
            "type": "filters.reprojection",
            "out_srs": TARGET_CRS,
        },
        {
            "type": "writers.las",
            "filename": OUTPUT_LAZ,
            "compression": True,
        },
    ]
}

pipeline = pdal.Pipeline(json.dumps(pipeline_def))
count = pipeline.execute()

print(f"\nWrote {count:,} points to {OUTPUT_LAZ}")
if count > 0:
    density = count / (width_m * height_m)
    print(f"Approx point density: {density:.2f} points/m^2")

# LiDAR Field Notes

Study notes from building the canopy and terrain classification pipeline in this repo. They cover the physics, the vocabulary, how each step works, and the bugs that taught me the most. The project itself is described in the [main README](../README.md).

**Data:** USGS 3DEP, project `MT_Statewide_P3_3_B21`
**Stack:** Python, PDAL, CloudCompare
**Scope:** about 0.25 km2 hillside clip, about 2 million points

---

## 1. How LiDAR works

I come from an oil and gas background, so the mental model that made LiDAR click was seismic reflection. The whole system comes down to one trick: send a pulse of energy out, time how long it takes to bounce back, and convert time to distance. Airborne LiDAR does what marine reflection seismic does, with light instead of sound and air instead of water and rock.

| Seismic reflection | Airborne LiDAR |
|---|---|
| Airgun pulse, towed behind a boat | Laser pulse, fired from a moving aircraft |
| Reflects at every rock-layer impedance contrast | Reflects off any solid surface (air itself never reflects) |
| One shot, multiple reflectors, one wiggle trace | One pulse, multiple bounces (canopy, branches, ground), multiple `returns` |
| Amplitude fades with each partial reflection | `Intensity` fades with each partial reflection |
| Streamer: many geophones per shot | One instrument fires and listens, closer to a single-channel echo sounder than a streamer spread |

### The beam is a narrow cone, not a point

Real lasers diverge slightly. At typical survey altitudes of 1,000 to 2,000 m with about 0.3 mrad of divergence, the ground footprint works out to roughly 20 cm to 1 m across, a dinner plate rather than a laser-pointer dot. A footprint that size can straddle a leaf and a gap at the same time, which is why one pulse produces several separate returns: it samples a small column of vertical structure.

### The scanning mirror

The laser fires in a fixed direction relative to the instrument, then hits an oscillating mirror that redirects each pulse to a different across-track angle. Combined with the aircraft's forward motion, this produces the classic sawtooth scan pattern on the ground.

### Multiple pulses in the air

The naive assumption is that the system waits for one pulse's echo before firing the next. At 1,000 m altitude the round trip takes about 6.7 microseconds, but at a 300 kHz pulse rate a pulse fires every 3.3 microseconds. That is faster than the previous echo gets back, so several pulses are in flight at once. This is a real technique called multiple pulses in the air (MPiA), and the underlying headache is called multiple time around (MTA). The receiver sorts out which echo belongs to which pulse using deliberately irregular pulse spacing, not wavelength tagging.

### Discrete-return versus full-waveform

The data I worked with (X, Y, Z, Intensity, ReturnNumber, NumberOfReturns) is discrete-return: someone already picked the peaks off the backscatter curve. Full-waveform LiDAR records the whole continuous curve for every pulse, the unpicked equivalent of a seismic trace.

### Two physical limits worth remembering

- **Minimum return separation.** Two reflectors closer than roughly 2 to 3 m vertically may blend into a single return. Short, dense shrubs are harder to resolve than tall, open-canopy forest. That is a physical limit, not a processing failure.
- **Why a roof and a treetop confuse a classifier.** Ground-finding algorithms reason about elevation geometry only. They never see materials, so a roof and a canopy top look identical: both are stuff floating above the locally lowest surface.

---

## 2. Vocabulary

### ASPRS classification codes

These codes are part of the LAS/LAZ file specification, so every LAS-reading tool agrees that 2 means ground.

| Code | Meaning | Where it came from in this project |
|---|---|---|
| 1 | Unclassified | 39.5% of the raw delivered points (786,345), and the non-ground label written by `filters.csf` |
| 2 | Ground | 60.5% of the raw delivered points were already labelled ground. Script 02 re-derives ground from geometry with `filters.csf`, which marks 61.9% of points |
| 3 | Low vegetation (under 0.5 m) | Height-above-ground threshold, 3.0% |
| 4 | Medium vegetation (0.5 to 2 m) | Height-above-ground threshold, 4.9% |
| 5 | High vegetation (2 m and up) | Height-above-ground threshold, 30.2% |
| 6 | Building | Hand-corrected in CloudCompare, 4,050 points covering 2 barns |
| 7 | Low noise | 1,612 points in the raw delivered data. The ground filter relabels them as ground or non-ground |
| 18 | High noise | 13 stray points, pre-flagged in the original delivery. The ground filter leaves them alone, and script 03 later folds them into the vegetation classes |

### Terms

- **Point cloud:** an unordered set of 3D points, the raw product of a LiDAR survey.
- **LAS / LAZ:** the standard point cloud file format, and its losslessly compressed version.
- **EPT (Entwine Point Tile):** an octree-organized format built for streaming huge datasets. A reader pulls only the tiles that intersect a bounding box instead of downloading everything.
- **DTM (Digital Terrain Model):** the bare-earth surface, built from ground points only.
- **DSM (Digital Surface Model):** the top-of-everything surface: canopy, roofs, and ground, whatever the laser hit first.
- **CHM (Canopy Height Model):** DSM minus DTM, which is how tall whatever is standing on the ground is.
- **Point density:** points per square meter. This clip has about 7.9 points per m2 overall.
- **CRS / EPSG code:** a coordinate reference system. Web Mercator (EPSG:3857) is what basemap viewers use for display. Its meters are stretched by roughly 45% at this latitude, so it is wrong for real area or density math. I cropped in it, which is fine for picking a box, then reprojected to UTM Zone 12N (EPSG:32612) before measuring anything.
- **ReturnNumber / NumberOfReturns:** which bounce a point is (1st, 2nd, and so on) versus how many bounces that pulse produced. The mean number of returns in this data is 1.785, and 45.5% of points came from multi-bounce pulses, even though canopy covers a minority of the actual area. Point-count statistics and area-coverage statistics tell different stories.
- **Intensity:** the strength of the returned pulse, which fades with each partial reflection.
- **CSF (Cloth Simulation Filter):** a ground-classification algorithm. It drapes a virtual cloth over the inverted point cloud, and the cloth settles on the lowest connected surface, held up by anything rigid such as trees and buildings. It is purely geometric.
- **HAG (Height Above Ground):** per-point elevation minus the nearest ground reference. `filters.hag_nn` uses a nearest-neighbor lookup and can produce hard Voronoi seams on sloped, sparsely covered terrain. `filters.hag_delaunay` interpolates smoothly using triangulation instead.

---

## 3. The pipeline in detail

### Step 1: Source the data

The data is free, public, and needs no login at `s3://usgs-lidar-public`, in EPT format. I browsed the Entwine viewer to find a wooded hillside, picked two corners with the viewer's point picker, and read the project's `ept.json` directly to get its real CRS instead of guessing. The environment is Miniconda with a dedicated `lidar` environment, because PDAL installs far more reliably from conda-forge than from pip.

### Step 2: Crop and reproject (`01_fetch_clip.py`)

`readers.ept` plus a bounding box, then `filters.reprojection` to UTM 12N, then `writers.las`. Because of EPT's octree structure, only the tiles overlapping the box are downloaded, never the whole statewide file of roughly 58 billion points. The result is a 601 m by 422 m clip with 1,992,375 points at about 7.9 points per m2.

### Step 3: First look by eye

I opened the clip in CloudCompare, colored by elevation and by the raw classification, and confirmed that the terrain relief and canopy clumps matched satellite imagery before trusting anything downstream. A hedgerow-looking streak turned out to be a real berm with a fence, which I checked against street-level imagery.

### Step 4: Ground classification and DTM/DSM/CHM (`02_dtm_dsm_chm.py`)

`filters.csf` classified 61.9% of points as ground, compared with 60.5% already labelled ground in the delivered data. The two sets of labels agree on 91.9% of points: 94.5% of the delivered ground is also ground to the filter, and 92.3% of the filter's ground is also delivered ground. That compares two algorithms, so it shows consistency rather than accuracy. I then gridded by hand with pandas (`groupby(["row","col"])["z"].agg(...)`) instead of using a raster library, to keep the mechanics visible. The DSM is the maximum Z per cell over all points, the DTM is the minimum Z per cell over ground-only points, and CHM is DSM minus DTM.

Findings: 7.1% of 1 m cells had no ground points at all, meaning dense canopy blocked every pulse from reaching bare earth. Mean CHM was 1.96 m, the maximum was 30.16 m, and the median was 0.18 m, because most of the scene is open field.

### Step 5: Vegetation height classes (`03_vegetation_classes.py`)

`filters.hag_nn` computes height above ground for each point, then thresholds sort non-ground points into classes 3, 4, and 5. The script carries a comment on its main limitation: height alone cannot tell a building from a tree, which is the same blind spot as CSF, inherited.

### Step 6: Manual correction

I used CloudCompare's Segment tool and its `#` shortcut, which stamps a scalar-field value onto everything inside a drawn polygon, to hand-label the two barns as class 6. This is the manual QA step that production LiDAR shops still do by hand. I confirmed the edit landed on disk, not just in the live session, by re-reading the saved file's classification counts: 4,050 points at class 6. Comparing the files before and after the edit showed where those points came from: 3,528 were vegetation (3,426 high, 101 medium, 1 low) and 522 were ground, which I have not examined further.

---

## 4. Debugging log

**Silent crash with no Python traceback.** Calling a conda environment's `python.exe` by raw path, instead of through `conda activate`, skips adding its `Library\bin` folder to `PATH`. Matplotlib's native rendering libraries then silently resolved to an incompatible version from a different Python install and hard-crashed the process with Windows fault code `0xc06d007f`. The fix is to prepend the environment's `Library/bin` to `PATH`, which is exactly what activation does.

**A computed field vanished on export.** `writers.las` writes only the standard LAS point-record fields by default, so a filter-computed extra like `HeightAboveGround` gets silently dropped. The fix is `"extra_dims": "all"` on the writer stage.

**laszip refused to save a manually edited file.** The error was that legacy flags and extended flags were not identical. This is a known LAS 1.4 interoperability gap: editing Classification through CloudCompare's GUI does not always keep its internal legacy and extended flag representations in sync, and laszip refuses to write a file it cannot validate. The workaround is to save uncompressed `.las`, or to drop to an older LAS point format.

**Nearest-neighbor HAG can fabricate a hard edge.** This one is theoretical and tested, but I did not see it at this resolution. Nearest-neighbor interpolation divides space into Voronoi cells, and on a slope with sparse ground coverage, two adjacent points can draw their nearest ground reference from opposite sides of a seam, creating a fake discontinuity. . `hag_delaunay` is the fix if it shows up.

**Crop-edge artifacts.** Ground and HAG algorithms need neighborhood context. Points right at an arbitrarily cropped boundary are missing their real neighbors just past the edge, so classification there degrades. A production workflow crops with a margin, classifies, then clips down to the final area of interest.

---

## 5. What I would do next

- Detect buildings from planarity instead of labelling them by hand.
- Compare against labelled reference data to get real accuracy numbers.
- Re-run with a buffered crop and `hag_delaunay`, and compare the results with this version.

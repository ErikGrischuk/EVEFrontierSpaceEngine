# EVE Frontier → SpaceEngine catalog converter

Turns CCP's EVE Frontier static data into a SpaceEngine add-on so you can fly the galaxy
in SE. Two scripts, same output format — pick your source.

```
python json_to_spaceengine.py solarsystemcontent.json out --names SolarSystems.csv --offset-pc 0 0 50000 --variety --ring-chance 0.45 --life-chance 0.25
```
##  Route  — `json_to_spaceengine.py` (recommended: real orbits)

Reads the big `solarsystemcontent.json` (FSD binary export, ~1.14 GB). It ships **real
orbital elements** (semi-major axis, eccentricity, orbit-plane normal, period) and physics
(mass, density, temperature, rotation), so you get true elliptical orbits and real planet
type names.

```
pip install ijson                 # C backend (yajl2) strongly recommended for speed
python json_to_spaceengine.py solarsystemcontent.json out --names SolarSystems.csv
```
- The JSON only stores a numeric `solarSystemNameID`, so `--names SolarSystems.csv` is used
  just to get readable system names ("O3H-1FN"). Optional; without it the system ID is used.
- Streamed with ijson → flat memory, handles the full 1.14 GB fine.
- **Zero-dependency:** if `ijson` isn't installed, the script auto-falls back to a built-in
  pure-stdlib streaming parser (slower, but no `pip install` needed). `ijson` just makes it
  much faster (seconds vs minutes on 1.14 GB).
- **Output is split into batched files** (SE loads many `.sc`/`.csv` from one folder):
  `--stars-per-file N` (default 20000 rows/CSV) and `--systems-per-file N`
  (default 2000 systems/.sc; a system's planets+moons never split across files).
  Use `--stars-per-file 0 --systems-per-file 0` for single-file output.

## Route 1 — `eve_to_spaceengine.py` (light, CSV only)
Reads `SolarSystems.csv` + `Planets.csv` + `Moons.csv`. Lighter, no dependencies, but the
CSV withholds orbital elements, so orbits are circular (bodies still sit at the right
distance/exact point). Good if you don't want to handle the 1.14 GB file.
```
python eve_to_spaceengine.py <folder_with_3_csvs> out
```

## Output → install
Both produce:
- `out/eve_frontier_stars.csv`  → copy to `SpaceEngine/addons/catalogs/stars/`
- `out/eve_frontier_planets.sc` → copy to `SpaceEngine/addons/catalogs/planets/`

Restart SpaceEngine → **F3**, type a system name (e.g. `O3H-1FN`) → **G** to fly there.

## How the data maps
| Source | → SpaceEngine |
|---|---|
| system `center` (abs. galactic metres) | star `RA / Dec / Dist` (parsecs; 1 pc = 3.0857e16 m) |
| star `spectralClass / temperature / mass / radius / luminosity` | `SpecClass / Temperature / MassSol / RadSol / AppMagn` |
| planet/moon orbit vectors + `eccentricity` + `orbitPeriod` | full `Orbit { SemiMajorAxis, Eccentricity, Inclination, AscendingNode, ArgOfPericenter, MeanAnomaly, Period }` |
| `radius`, `massDust+massGas`, `rotationRate` | `Radius` (km), `Mass` (Earth masses), `RotationPeriod` (hours) |
| `typeID` + `typeDescription` | planet `Class` (gas-giant override for giant-sized bodies) |

## Accuracy notes (verified numerically)
- **Planets** reproduce their exact in-game 3D position to ~1e-9 (machine precision).
- **Moons**: the JSON's moon `position` is only ~85% self-consistent with its own orbit
  vectors (EVE's moon data is approximate), so moon *phase* is best-effort while orbit
  *shape* is exact. Visually negligible (moons are tiny).
- Bodies move in SE real-time from the epoch snapshot — **freeze time** in SE for a static
  map matching the game exactly.
- Sentinel/empty stars (`O0`, `radius = 4294967296` = 2³², `mass = Infinity`) are placed in
  space with physics left for SE to solve.

## Moving the cluster into empty space (`--offset-pc`)
By default EVE Frontier lands ~170–2500 pc from Sol, mixed in with Milky Way stars. To make
it read as its own separate "star city" out in empty space, shift the whole cluster:
```
--offset-pc 0 0 30000     # push 30000 pc "up" out of the galactic plane
```
This is a **rigid XYZ translation** (parsecs), so every relative position inside the cluster
stays exact (verified to ~1e-6 pc) and you can still fly between systems normally — only the
cluster's location relative to Sol changes. (Don't fake this by adding to `Dist`; a radial add
distorts the geometry. The translation is applied in the original metre coordinate frame
before RA/Dec/Dist is computed.)

## Nebula effect around the real stars (`--nebula`)
SpaceEngine **cannot put real catalog stars inside a separate distant `Galaxy` blob** — a
`Galaxy {}` object is just a painted sprite with procedural (fake) stars; flying to it will
NOT show your EVE systems. Catalog stars always live in the Milky Way, placed by RA/Dec/Dist
around the Sun.

To get the "galaxy / nebula" look *around your actual flyable stars*, co-locate a real
**Nebula** with the cluster:
```
--nebula                       # auto-centres a Diffuse nebula on the cluster
--nebula-mag 4                 # brightness (lower = brighter)
--nebula-scale 1.3             # multiply auto radius (fatter glow)
```
This writes `output/catalogs/nebulae/eve_frontier_nebula.sc`. The nebula is centred on the
cluster centroid with a radius covering ~90% of the systems, so the stars sit *inside* the
glow. It follows `--offset-pc` automatically. Fine-tune position/size/brightness live in SE
Edit mode (press `*` twice) — no re-run needed.

> Put the file in your addon's `catalogs/nebulae/` folder. Keep your distant `Galaxy` block
> only as far-away decoration, or remove it — it's separate from the explorable cluster.

## Planet variety (`--variety`) — atmospheres, oceans, clouds, life, rings, auroras
By default the converter emits the hard physics (class, radius, mass, exact orbit) and lets
SpaceEngine fill in surface detail procedurally. `--variety` adds deliberate, **deterministic**
optional tags to each planet/moon, grounded in the real EVE data where it exists:
- **Atmosphere** — added when EVE `pressure > 0`; `Pressure` set from the real value, model picked
  by class/temperature (Earth / Venus / Jupiter / Neptune / Pluto / Titan / Mars).
- **Ocean** — on (non-boiling) Oceania worlds, and on habitable Terra worlds with an atmosphere.
- **Clouds** — on worlds with an atmosphere; tidally-locked worlds (EVE `locked`) get a single giant
  cyclone (`TidalLocked true`).
- **Life** — always when EVE's `life` flag is set; otherwise on habitable worlds
  (250–340 K + atmosphere) with probability `--life-chance` (default 0.25), plus rare subglacial
  life on cold ice worlds. Lava worlds get `NoLife`.
- **Rings** — on a deterministic subset of gas giants (`--ring-chance`, default 0.45); rare on large
  rocky worlds; never on moons.
- **Aurora** — rare, on gas giants with an atmosphere.
```
--variety                      # turn it on
--ring-chance 0.45             # fraction of gas giants with rings
--life-chance 0.25             # chance of life on a habitable world (EVE-flagged life always added)
```
Deterministic per body name, so reruns are identical. Tweak any feature live in SE Edit mode.

## Coloured nebulae — the in-game "Nebula Type" map
In EVE each system carries a *nebula-type* tag (8 types, ~24k systems = every system). Both modes
below emit coloured procedural `Diffuse` nebula blobs (colour via `emParticleColor`, no textures —
SE has no per-nebula `Color` param) into two files you copy into your addon at the matching paths:
- `catalogs/nebulae/<file>.sc`  — the nebula objects
- `models/nebulae/<file>.cfg`   — procedural colour models (`UseForObject`)

Shared tunables (apply to both modes):
```
--field-cell-pc 150            # grid cell (pc) for down-sampling blobs (bigger = fewer/larger)
--field-max-blobs 40           # max blobs per type (caps object count / SE load)
--field-mag 5                  # brightness per blob (lower = brighter)
--field-radius-scale 1.0       # multiply blob radius (overlap = smoother cloud)
```

### `--nebula-map nebulas.txt` — EXACT 1:1 (recommended)
Reads the in-game export (`nebulas.txt`, TSV with `Solar System ID` + `Nebula ID type`) and places
each system into its **real** nebula type, then emits coloured blobs at the true positions →
`eve_frontier_nebulae.{sc,cfg}`. This reproduces the game map exactly. The System ID joins directly
to the JSON's `solarSystemID`. For the full galaxy, raise `--field-max-blobs` (e.g. 100–150) to
capture more structure per type.
```
--nebula-map "...\nebulas.txt" --field-max-blobs 120
```
Type→colour: Gamma Ray=orange, Hydrogen Alpha=red, Millimeter=yellow, Visible Light=green,
Molecular Gas=cyan, X-Ray=blue, Ultraviolet=violet, Infrared=dark red.

### `--nebula-field` — APPROXIMATION (only if you lack the mapping)
Bins systems by distance from the core into the 8 zones (in-game counts as shell sizes),
reproducing the core→rim gradient → `eve_frontier_field.{sc,cfg}`. Use only when you don't have
`nebulas.txt`.

Tweak any blob's position/size live in SE Edit mode (`*` twice).

## Why a star cluster near Sol, not a separate "EVE Frontier" galaxy
Per the SE manual, the star catalogs (`.sc`/`.csv`) position every system by **RA/Dec/Dist
relative to the Sun**, inside the Milky Way. There is *no* supported way to drop real,
named system catalogs into a custom distant `Galaxy` object — a custom Galaxy is just a
fuzzy sprite blob whose interior stars are generated procedurally (fake). So tagging stars
with `Galaxy "EVE Frontier"` at a huge distance does **not** place them there; SE falls back
to RA/Dec/Dist and the positions come out wrong. This converter therefore renders EVE
Frontier as a **navigable star cluster ~150–600 pc from Sol**, which keeps every real
position correct and flyable. (That's also the difference vs. an earlier `Galaxy`-wrapper
attempt that exported wrong positions.)

## Tuning (top of each script)
- `TYPE_TO_CLASS` — EVE planet type IDs → SE classes (11 Temperate, 12 Ice, 13 Gas,
  2014 Oceanic, 2015 Lava, 2016 Barren, 2017 Storm, 2063 Plasma, 14 Moon). Verify class
  strings against your SE build (e.g. `Oceania` vs `Aquaria`).
- `INCLUDE_PERIOD`, `INCLUDE_ROTATION` — toggle Orbit `Period` (years) and `RotationPeriod`
  (hours). Disable rotation if your SE build expects a different unit.

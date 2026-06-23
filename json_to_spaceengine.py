#!/usr/bin/env python3
"""
EVE Frontier (solarsystemcontent.json, FSD binary export) -> SpaceEngine catalogs.

This is the "rich" route: the 1.14 GB JSON ships REAL orbital elements
(orbitSemiMajorAxis / orbitSemiMinorAxis / eccentricity / orbitPlaneNormal /
orbitEllipseCenter / orbitPeriod) and physics (mass, density, temperature,
rotationRate), so we get true elliptical orbits instead of the circular
approximation the CSV route had to use.

The file is streamed with ijson, so memory stays flat regardless of file size.

    pip install ijson           # (yajl2 C backend strongly recommended for speed)
    python json_to_spaceengine.py solarsystemcontent.json out [--names SolarSystems.csv]

Outputs:
    out/eve_frontier_stars.csv   -> SpaceEngine/addons/catalogs/stars/
    out/eve_frontier_planets.sc  -> SpaceEngine/addons/catalogs/planets/

--names is optional: the JSON only stores a numeric solarSystemNameID, so pass the
CSV's SolarSystems.csv to get readable names ("O3H-1FN"); otherwise the system ID is used.

JSON shape:
    { "Type: FSD Multi Index": [ {"<id>":"<id>"}, {"<id>":{...system...}}, ... ] }
    system.center / planet.position / moon.position / orbit* vectors are
        [ {vector_schema...}, "x", "y", "z" ]  (metres; values are strings).
    planet.position is RELATIVE TO THE STAR; moon.position is RELATIVE TO THE STAR too,
    but a moon's orbit* vectors are RELATIVE TO ITS PLANET.
"""
import argparse, csv, hashlib, json, math, os, sys

# ---------------- config -----------------
INCLUDE_PERIOD   = True     # Orbit Period (years).  Verified vs Kepler; SE can also derive it.
INCLUDE_ROTATION = True     # RotationPeriod (hours). Verified vs SE manual examples.
# Output is split into batched files (SE happily loads many .sc / .csv files in a folder).
# 0 = single file. Defaults are sane for the full galaxy; override via CLI.
STARS_PER_FILE   = 20000    # star rows per CSV file
SYSTEMS_PER_FILE = 2000     # systems per planets .sc file (a system is never split)
EARTH_MASS = 5.97237e24     # kg

PC   = 3.0856775814913673e16
AU   = 1.495978707e11
LSUN = 3.828e26
MSUN = 1.98892e30
RSUN = 6.957e8
YEAR = 31557600.0           # s (Julian year)
SENT = 4294967296.0         # 2**32 CCP "null" sentinel
BLACKHOLE_MASS_SOL = 10.0    # EVE doesn't store BH mass; SE needs MassSol to size it (adjustable)

TYPE_TO_CLASS = {
    11:"Terra", 12:"Ice", 13:"GasGiant", 2014:"Oceania", 2015:"Lava",
    2016:"Selena", 2017:"GasGiant", 2063:"Lava", 14:"Selena",
}
TYPE_NAME = {11:"Temperate",12:"Ice",13:"Gas",2014:"Oceanic",2015:"Lava",
             2016:"Barren",2017:"Storm",2063:"Plasma",14:"Moon"}
ROMAN = ["","I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII","XIII","XIV",
         "XV","XVI","XVII","XVIII","XIX","XX","XXI","XXII","XXIII","XXIV","XXV","XXVI",
         "XXVII","XXVIII","XXIX","XXX","XXXI","XXXII"]


def jf(v):
    """string/number -> float or None (rejects '', Infinity, NaN, sentinel)."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def vec(o):
    """[ {schema}, 'x','y','z' ] -> (x,y,z) floats."""
    if not isinstance(o, list) or len(o) < 4:
        return None
    try:
        return (float(o[-3]), float(o[-2]), float(o[-1]))
    except (TypeError, ValueError):
        return None


def roman(i):
    return ROMAN[i] if 0 <= i < len(ROMAN) else str(i)


def choose_class(tid, radius_m, desc):
    """typeID -> SE class. EVE's own type is authoritative. A 'giant' in the
    description promotes odd lava/rock types (e.g. 'Plasma Giant') to gas giants.
    We do NOT override by size alone: EVE has huge low-density 'super-planets'
    (e.g. 'Temperate Super-Planet', ~6x Earth radius, 279 K) that are still
    terrestrial worlds, not gas giants."""
    d = (desc or "").lower()
    base = TYPE_TO_CLASS.get(tid, "Selena")
    if base == "GasGiant":
        return base
    if "giant" in d:                         # 'Plasma Giant', 'Puffy Giant', ...
        return "GasGiant"
    return base


def _h01(key):
    """deterministic 0..1 from a string key (stable across reruns)."""
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xffffffff


def _truthy(v):
    return str(v).strip().lower() in ("1", "1.0", "true", "yes")


def variety_tags(name, cls, stats, radius_m, is_moon, ring_chance, life_chance):
    """Give each body realistic optional features (Atmosphere, Ocean, Clouds, Life,
    Rings, Aurora) -- grounded in EVE data where available (pressure, life flag,
    tidal lock, temperature) and otherwise procedural/deterministic per body name.
    Returns a list of tab-indented SE lines to insert inside the body block."""
    L = []
    temp = jf(stats.get("temperature"))
    press_pa = jf(stats.get("pressure")) or 0.0
    press_atm = press_pa / 101325.0
    locked = _truthy(stats.get("locked"))
    eve_life = _truthy(stats.get("life"))
    rkm = (radius_m or 0) / 1000.0
    hot = temp is not None and temp > 340
    cold = temp is not None and temp < 250
    hab = temp is not None and 250 <= temp <= 340
    has_atm = press_pa > 0
    r = lambda salt: _h01(f"{name}|{salt}")

    # ---- atmosphere (presence + density from EVE pressure) ----
    if has_atm:
        if cls == "GasGiant":
            model = "Neptune" if cold else "Jupiter"
        elif cls == "Lava" or hot:
            model = "Venus"
        elif cls in ("Terra", "Oceania"):
            model = "Earth"
        elif cls == "Ice":
            model = "Pluto" if cold else "Titan"
        else:
            model = "Mars"
        if press_atm >= 0.01:
            L.append(f'\tAtmosphere {{ Model "{model}"  Pressure {press_atm:.4g} }}')
        else:
            L.append(f'\tAtmosphere {{ Model "{model}" }}')
    elif cls == "Selena" or (cls == "Lava" and not is_moon):
        L.append('\tNoAtmosphere true')

    # ---- ocean ----
    if (cls == "Oceania" and not hot) or (cls == "Terra" and hab and has_atm):
        L.append('\tOcean {}')

    # ---- clouds (need an atmosphere; tidal lock -> one giant cyclone) ----
    if has_atm and cls in ("Terra", "Oceania", "GasGiant"):
        L.append('\tClouds { TidalLocked true }' if locked else '\tClouds {}')

    # ---- life ----
    organic_ok = cls in ("Terra", "Oceania") and hab and has_atm
    if eve_life and cls in ("Terra", "Oceania"):
        L.append('\tLife { Class "Organic" Type "Multicellular" Biome "Marine/Terrestrial" }')
    elif organic_ok and r("life") < life_chance:
        biome = "Marine" if cls == "Oceania" else "Marine/Terrestrial"
        L.append(f'\tLife {{ Class "Organic" Type "Multicellular" Biome "{biome}" }}')
    elif cls == "Ice" and cold and r("subglacial") < life_chance * 0.5:
        L.append('\tLife { Class "Organic" Type "Unicellular" Biome "Subglacial" }')
    elif cls == "Lava":
        L.append('\tNoLife true')

    # ---- rings (gas giants mostly; rare on big rocky worlds; never moons) ----
    if not is_moon and cls == "GasGiant" and r("rings") < ring_chance:
        L.append('\tRings {}')
    elif not is_moon and cls in ("Terra", "Oceania") and rkm > 8000 and r("trings") < ring_chance * 0.15:
        L.append('\tRings {}')

    # ---- aurora (rare, gas giants with an atmosphere) ----
    if has_atm and cls == "GasGiant" and r("aurora") < 0.12:
        L.append('\tAurora {}')

    return L


# ---------------- vector helpers ----------------
def _n(v):
    m = math.sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2])
    return ((v[0]/m, v[1]/m, v[2]/m), m) if m else ((0.0,0.0,0.0), 0.0)
def _cross(a,b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def _dot(a,b):   return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]


def kepler_orbit(stats, r_rel):
    """Build SE orbital elements from EVE orbit vectors + current position r_rel
    (position relative to the parent body). Returns dict or None.

    Convention (validated against the data):
      * a       = |orbitSemiMajorAxis vector|
      * peri dir= +orbitSemiMajorAxis_hat   (== -orbitEllipseCenter_hat for planets)
      * plane   from orbitPlaneNormal
      * phase   from the body's position, projected into the orbit plane
    Planet positions reproduce to ~1e-9 (exact). Moon positions in this export are
    only ~self-consistent to ~15%, so moon phase is best-effort (orbit shape stays
    correct); visually negligible.
    """
    a_vec = vec(stats.get("orbitSemiMajorAxis"))
    nrm   = vec(stats.get("orbitPlaneNormal"))
    e     = jf(stats.get("eccentricity")) or 0.0
    if not a_vec or not nrm or not r_rel:
        return circular_orbit(r_rel)            # fallback (no orbit vectors)
    nh, _  = _n(nrm)
    ph, a  = _n(a_vec)                           # periapsis dir + semi-major axis length
    if a == 0 or nh == (0.0,0.0,0.0):
        return circular_orbit(r_rel)
    # position projected into the orbit plane -> in-plane radial direction
    rd  = _dot(r_rel, nh)
    rp  = (r_rel[0]-rd*nh[0], r_rel[1]-rd*nh[1], r_rel[2]-rd*nh[2])
    rh, rpm = _n(rp)
    if rpm == 0:
        rh = ph
    inc = math.degrees(math.acos(max(-1.0, min(1.0, nh[2]))))
    N   = _cross((0,0,1), nh)
    Nh, Nm = _n(N)
    if Nm < 1e-9:                                # equatorial orbit
        node = 0.0
        w  = math.degrees(math.atan2(ph[1], ph[0])) % 360
    else:
        node = math.degrees(math.atan2(Nh[1], Nh[0])) % 360
        w  = math.degrees(math.atan2(_dot(_cross(Nh, ph), nh), _dot(Nh, ph))) % 360
    nu = math.atan2(_dot(_cross(ph, rh), nh), _dot(ph, rh))
    E  = math.atan2(math.sqrt(max(0.0, 1-e*e))*math.sin(nu), e+math.cos(nu))
    M  = math.degrees(E - e*math.sin(E)) % 360
    return dict(a=a, e=e, inc=inc, node=node, argp=w, mean=M)


def circular_orbit(r_rel):
    """No orbit vectors -> circular orbit through the point (min-inclination plane)."""
    if not r_rel:
        return None
    rh, rm = _n(r_rel)
    if rm == 0:
        return None
    k = (0.0,0.0,1.0) if abs(rh[2]) <= 0.999999 else (1.0,0.0,0.0)
    kr = _dot(k, rh)
    h  = (k[0]-kr*rh[0], k[1]-kr*rh[1], k[2]-kr*rh[2])
    nh, _ = _n(h)
    inc = math.degrees(math.acos(max(-1.0, min(1.0, nh[2]))))
    N = _cross((0,0,1), nh); Nh, Nm = _n(N)
    if Nm < 1e-9:
        node = 0.0; u = math.degrees(math.atan2(r_rel[1], r_rel[0]))
    else:
        node = math.degrees(math.atan2(Nh[1], Nh[0])) % 360
        cu = max(-1.0, min(1.0, _dot(Nh, rh)))
        u = math.degrees(math.acos(cu))
        if r_rel[2] < 0: u = 360 - u
    return dict(a=rm, e=0.0, inc=inc % 360, node=node % 360, argp=0.0, mean=u % 360)


def stream_items(path):
    """Yield each element of the top-level "Type: FSD Multi Index" array, one at a
    time, without loading the 1.14 GB file into memory.

    Prefers ijson (fast C backend) if installed; otherwise falls back to a built-in
    pure-stdlib streaming scanner so NO pip install is required (just slower)."""
    try:
        import ijson
        with open(path, "rb") as fh:
            for item in ijson.items(fh, "Type: FSD Multi Index.item"):
                yield item
        return
    except ImportError:
        sys.stderr.write("[info] 'ijson' not installed -> using built-in stream parser "
                         "(slower; `pip install ijson` for speed).\n")

    # Fallback: char-level scanner. Finds the first '[', then yields each top-level
    # {...} element (string-/escape-aware brace matching) until the matching ']'.
    with open(path, "r", encoding="utf-8") as f:
        started = capturing = in_str = esc = False
        depth = 0; buf = []
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            for ch in chunk:
                if not started:
                    if ch == "[":
                        started = True
                    continue
                if capturing:
                    buf.append(ch)
                    if in_str:
                        if esc:        esc = False
                        elif ch == "\\": esc = True
                        elif ch == '"': in_str = False
                    elif ch == '"':    in_str = True
                    elif ch == "{":    depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            yield json.loads("".join(buf))
                            capturing = False; buf = []
                elif ch == "{":
                    capturing = True; depth = 1; buf = ["{"]
                elif ch == "]":
                    return


class StarCsvWriter:
    """Writes star rows, rotating to a new numbered CSV every `per_file` rows."""
    def __init__(self, outdir, per_file):
        self.outdir = outdir; self.per_file = per_file
        self.n = 0; self.idx = 0; self.fh = None; self.w = None; self.files = []
    def _rotate(self):
        if self.fh: self.fh.close()
        self.idx += 1
        suffix = "" if self.per_file <= 0 else f"_{self.idx:04d}"
        path = os.path.join(self.outdir, f"eve_frontier_stars{suffix}.csv")
        self.files.append(path)
        self.fh = open(path, "w", newline="", encoding="utf-8")
        self.w = csv.writer(self.fh)
        self.w.writerow(["Name","RA","Dec","Dist","AppMagn","SpecClass",
                         "MassSol","RadSol","Temperature"])
    def row(self, r):
        if self.fh is None or (self.per_file > 0 and self.n % self.per_file == 0):
            self._rotate()
        self.w.writerow(r); self.n += 1
    def close(self):
        if self.fh: self.fh.close()


class ScWriter:
    """Writes per-system blocks, rotating to a new numbered .sc every `per_file`
    systems (a system's planets+moons always stay together in one file)."""
    def __init__(self, outdir, per_file):
        self.outdir = outdir; self.per_file = per_file
        self.n = 0; self.idx = 0; self.fh = None; self.files = []
    def _rotate(self):
        if self.fh: self.fh.close()
        self.idx += 1
        suffix = "" if self.per_file <= 0 else f"_{self.idx:04d}"
        path = os.path.join(self.outdir, f"eve_frontier_planets{suffix}.sc")
        self.files.append(path)
        self.fh = open(path, "w", encoding="utf-8")
        self.fh.write("// EVE Frontier -> SpaceEngine planets catalog (from solarsystemcontent.json)\n")
        self.fh.write("// Place in: SpaceEngine/addons/catalogs/planets/\n\n")
    def system(self, text):
        if self.fh is None or (self.per_file > 0 and self.n % self.per_file == 0):
            self._rotate()
        self.fh.write(text); self.n += 1
    def close(self):
        if self.fh: self.fh.close()


def galactic_to_radecdist(x, y, z):
    r = math.sqrt(x*x+y*y+z*z)
    return ((math.atan2(y, x) % (2*math.pi))*12/math.pi,
            math.degrees(math.asin(z/r)) if r else 0.0, r/PC)


# ---------------- writers ----------------
def write_orbit(out, o, refplane, period_s):
    out.append("\tOrbit")
    out.append("\t{")
    out.append(f'\t\tRefPlane        "{refplane}"')
    out.append(f'\t\tSemiMajorAxis   {o["a"]/AU:.8f}')
    out.append(f'\t\tEccentricity    {o["e"]:.6f}')
    out.append(f'\t\tInclination     {o["inc"]:.4f}')
    out.append(f'\t\tAscendingNode   {o["node"]:.4f}')
    out.append(f'\t\tArgOfPericenter {o["argp"]:.4f}')
    out.append(f'\t\tMeanAnomaly     {o["mean"]:.4f}')
    if INCLUDE_PERIOD and period_s:
        out.append(f'\t\tPeriod          {period_s/YEAR:.8f}')
    out.append("\t}")


def body_block(out, tag, name, parent, cls, stats, radius_m, orbit, refplane, type_note,
               variety=False, ring_chance=0.45, life_chance=0.25):
    out.append(f'{tag} "{name}"')
    out.append("{")
    out.append(f'\tParentBody  "{parent}"')
    out.append(f'\tClass       "{cls}"        // {type_note}')
    if radius_m:
        out.append(f'\tRadius      {radius_m/1000.0:.3f}')
    mass = (jf(stats.get("massDust")) or 0) + (jf(stats.get("massGas")) or 0)
    if mass > 0:
        out.append(f'\tMass        {mass/EARTH_MASS:.6g}')   # Earth masses
    if INCLUDE_ROTATION:
        rot = jf(stats.get("rotationRate"))
        if rot and rot > 0:
            out.append(f'\tRotationPeriod {rot/3600.0:.4f}')  # hours -- verify unit
    if variety:
        out.extend(variety_tags(name, cls, stats, radius_m, tag == "Moon",
                                ring_chance, life_chance))
    if orbit:
        write_orbit(out, orbit, refplane, jf(stats.get("orbitPeriod")))
    out.append("}")


# Canonical EVE Frontier nebula types: in-game "Nebula ID type" -> name.
NEBULA_ID_NAMES = {
    "27931": "Gamma Ray",      "27933": "Millimeter Waves", "27932": "Hydrogen Alpha",
    "27934": "Visible Light",  "27936": "X-Ray",            "27930": "Molecular Gas",
    "27935": "Ultraviolet",    "27033": "Infrared",
}
# emParticleColor RGB (0..1) per nebula type, matching the in-game map colours.
NEBULA_TYPE_COLORS = {
    "Hydrogen Alpha":   (1.00, 0.12, 0.12),   # deep red
    "Gamma Ray":        (1.00, 0.40, 0.12),   # orange
    "Millimeter Waves": (1.00, 0.85, 0.20),   # yellow
    "Visible Light":    (0.30, 0.90, 0.30),   # green
    "Molecular Gas":    (0.20, 0.85, 0.92),   # cyan
    "X-Ray":            (0.30, 0.45, 1.00),   # blue
    "Ultraviolet":      (0.62, 0.30, 0.95),   # violet
    "Infrared":         (0.60, 0.16, 0.06),   # dark red/brown
}
# Radial order (core -> rim) + in-game counts -- used ONLY by the --nebula-field
# approximation when no exact per-system mapping is supplied.
NEBULA_ZONES = [
    ("Hydrogen Alpha", 3294), ("Gamma Ray", 9265), ("Millimeter Waves", 6823),
    ("Visible Light", 2041),  ("Molecular Gas", 742), ("X-Ray", 1268),
    ("Ultraviolet", 593),
]


def write_nebula_blobs(outdir, zone_points, cell_pc, max_blobs, mag, rscale, fname, comment):
    """Emit coloured procedural diffuse nebula blobs for a {type_name: [positions]} dict.
    Down-samples each zone onto a grid, caps blob count, writes <fname>.sc + <fname>.cfg."""
    cell_m = cell_pc * PC
    nebs, models = [], []
    for name, pts in zone_points.items():
        if not pts:
            continue
        color = NEBULA_TYPE_COLORS.get(name, (0.80, 0.80, 0.80))
        cells = {}
        for (x, y, z) in pts:
            key = (round(x/cell_m), round(y/cell_m), round(z/cell_m))
            c = cells.setdefault(key, [0.0, 0.0, 0.0, 0])
            c[0] += x; c[1] += y; c[2] += z; c[3] += 1
        centers = [(v[0]/v[3], v[1]/v[3], v[2]/v[3]) for v in cells.values()]
        if len(centers) > max_blobs:                       # even stride down-sample
            step = len(centers)/max_blobs
            centers = [centers[int(k*step)] for k in range(max_blobs)]
        rad_pc = cell_pc * 0.85 * rscale
        slug = name.replace(" ", "")
        for k, (x, y, z) in enumerate(centers, 1):
            ra, dec, dist = galactic_to_radecdist(x, y, z)
            obj = f"EVE {name} {k}"
            nebs.append((obj, ra, dec, dist, rad_pc))
            models.append((f"{slug}{k}Model", obj, color))
    if not nebs:
        return None

    ndir = os.path.join(outdir, "catalogs", "nebulae")
    mdir = os.path.join(outdir, "models", "nebulae")
    os.makedirs(ndir, exist_ok=True); os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(ndir, f"{fname}.sc"), "w", encoding="utf-8") as fh:
        fh.write(f"// {comment}\n")
        for obj, ra, dec, dist, rad in nebs:
            fh.write(f'Nebula "{obj}"\n{{\n')
            fh.write('    Galaxy  "Milky Way"\n    Type    "Diffuse"\n')
            fh.write(f'    RA      {ra:.7f}\n    Dec     {dec:.7f}\n')
            fh.write(f'    Dist    {dist:.5f}\n    Radius  {rad:.5f}\n')
            fh.write(f'    AppMagn {mag:g}\n}}\n')
    with open(os.path.join(mdir, f"{fname}.cfg"), "w", encoding="utf-8") as fh:
        fh.write(f"// Procedural coloured diffuse models -- {comment}\n")
        for mname, obj, (r, g, b) in models:
            fh.write(f'NebulaModel "{mname}"\n{{\n')
            fh.write(f'    UseForObject    "{obj}"\n')
            fh.write('    Method          "Diffuse"\n')
            fh.write(f'    emParticleColor ({r:.3f} {g:.3f} {b:.3f})\n')
            fh.write(f'    sumColor        ({int(r*255)} {int(g*255)} {int(b*255)})\n')
            fh.write('    clipRadius      1\n}\n')
    return len(nebs), len([z for z in zone_points if zone_points[z]])


def radial_zone_points(pts):
    """Approximation: assign systems to spectral zones by distance from the core,
    using in-game counts as shell sizes.  Returns {type_name: [positions]}."""
    n = len(pts)
    if n < 2:
        return {}
    cx = sum(p[0] for p in pts)/n
    cy = sum(p[1] for p in pts)/n
    cz = sum(p[2] for p in pts)/n
    order = sorted(range(n),
                   key=lambda i: (pts[i][0]-cx)**2+(pts[i][1]-cy)**2+(pts[i][2]-cz)**2)
    total = sum(c for _, c in NEBULA_ZONES)
    zp = {name: [] for name, _ in NEBULA_ZONES}
    cursor = 0
    for name, count in NEBULA_ZONES:
        share = int(round(n * count / total))
        for i in order[cursor:cursor+share]:
            zp[name].append(pts[i])
        cursor += share
    if cursor < n:                                        # leftover -> outermost zone
        for i in order[cursor:]:
            zp[NEBULA_ZONES[-1][0]].append(pts[i])
    return zp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json")
    ap.add_argument("outdir")
    ap.add_argument("--names", help="SolarSystems.csv to map solarSystemId -> readable name")
    ap.add_argument("--planet-refplane", default="Ecliptic")
    ap.add_argument("--moon-refplane",   default="Equator")
    ap.add_argument("--stars-per-file",   type=int, default=STARS_PER_FILE,
                    help="star rows per CSV file (0 = single file)")
    ap.add_argument("--systems-per-file", type=int, default=SYSTEMS_PER_FILE,
                    help="systems per planets .sc file (0 = single file)")
    ap.add_argument("--offset-pc", type=float, nargs=3, metavar=("DX","DY","DZ"),
                    default=[0.0, 0.0, 0.0],
                    help="shift the whole cluster by DX DY DZ parsecs (rigid translation; "
                         "keeps relative positions exact). e.g. '--offset-pc 0 0 30000' "
                         "pushes it out of the galactic plane into empty space.")
    ap.add_argument("--nebula", action="store_true",
                    help="also emit catalogs/nebulae/eve_frontier_nebula.sc -- a diffuse "
                         "nebula auto-centred on the cluster so the stars sit INSIDE a glow.")
    ap.add_argument("--nebula-mag", type=float, default=4.0,
                    help="apparent magnitude of the nebula (lower = brighter; tune in Edit mode)")
    ap.add_argument("--nebula-scale", type=float, default=1.0,
                    help="multiply the auto-computed nebula radius (e.g. 1.3 = fatter glow)")
    ap.add_argument("--variety", action="store_true",
                    help="add realistic optional features to planets/moons: atmospheres "
                         "(from EVE pressure), oceans, clouds (tidal-locked cyclones), life "
                         "(EVE life flag + habitable worlds), rings on gas giants, rare auroras. "
                         "Deterministic per body; SE also fills the rest procedurally.")
    ap.add_argument("--ring-chance", type=float, default=0.45,
                    help="fraction of gas giants that get rings with --variety (default 0.45).")
    ap.add_argument("--life-chance", type=float, default=0.25,
                    help="chance of life on a habitable world with --variety (default 0.25); "
                         "EVE-flagged life is always added regardless.")
    ap.add_argument("--nebula-map", metavar="TSV",
                    help="EXACT in-game nebula map: path to the nebulas.txt export "
                         "(columns incl. 'Solar System ID' + 'Nebula ID type'). Buckets each "
                         "system into its real nebula type and emits coloured diffuse blobs.")
    ap.add_argument("--nebula-field", action="store_true",
                    help="APPROXIMATE the in-game coloured 'Nebula Type' map: bin systems by "
                         "distance from the core into 8 spectral zones and emit coloured diffuse "
                         "nebula blobs (sc + procedural colour models, no textures). Use this only "
                         "if you don't have the nebulas.txt mapping for --nebula-map.")
    ap.add_argument("--field-cell-pc", type=float, default=150.0,
                    help="grid cell (pc) for down-sampling field blobs; bigger = fewer, larger blobs")
    ap.add_argument("--field-max-blobs", type=int, default=40,
                    help="max blobs per spectral zone (caps object count / SE load)")
    ap.add_argument("--field-mag", type=float, default=5.0,
                    help="apparent magnitude of each field blob (lower = brighter)")
    ap.add_argument("--field-radius-scale", type=float, default=1.0,
                    help="multiply field blob radius (overlap = smoother cloud)")
    args = ap.parse_args()
    off_m = [v*PC for v in args.offset_pc] if any(args.offset_pc) else None
    # collect system centres (metres, post-offset) for nebula / field
    neb_pts = [] if (args.nebula or args.nebula_field) else None

    # exact per-system nebula type mapping (--nebula-map nebulas.txt)
    neb_id_to_type = None
    zone_points_map = None
    if args.nebula_map:
        neb_id_to_type = {}
        with open(args.nebula_map, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                tid = (r.get("Nebula ID type") or "").strip()
                name = NEBULA_ID_NAMES.get(tid)
                if name:
                    neb_id_to_type[(r.get("Solar System ID") or "").strip()] = name
        zone_points_map = {}
        print(f"  loaded nebula map: {len(neb_id_to_type)} systems")

    os.makedirs(args.outdir, exist_ok=True)
    names = {}
    if args.names and os.path.exists(args.names):
        with open(args.names, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                names[row["solarSystemId"]] = (row.get("name") or "").strip()

    star_w = StarCsvWriter(args.outdir, args.stars_per_file)
    sc_w   = ScWriter(args.outdir, args.systems_per_file)

    n_sys = n_pl = n_mn = n_bh = 0
    for item in stream_items(args.json):
            if not isinstance(item, dict):
                continue
            sid, s = next(iter(item.items()))
            if not isinstance(s, dict) or "center" not in s:
                continue                                # index marker -> skip
            c = vec(s.get("center"))
            if not c:
                continue
            if off_m:                                   # rigid XYZ translation (keeps geometry)
                c = [c[0]+off_m[0], c[1]+off_m[1], c[2]+off_m[2]]
            if neb_pts is not None:
                neb_pts.append(c)
            if zone_points_map is not None:
                nm = neb_id_to_type.get(str(sid))
                if nm:
                    zone_points_map.setdefault(nm, []).append(c)
            sname = names.get(sid) or sid
            ra, dec, dist = galactic_to_radecdist(*c)

            st = (s.get("star") or {}).get("statistics", {}) or {}
            teff = jf(st.get("temperature")); teff = teff if (teff and 700 < teff < 120000) else None
            spec = (st.get("spectralClass") or "").strip()
            if spec in ("0.0", "0", ""): spec = ""

            # EVE Frontier black holes: placeholder star with mass "Infinity" and
            # radius = 2**32 sentinel. Render as a real SpaceEngine black hole (Class "X").
            raw_mass = str(st.get("mass") or "").strip().lower()
            try: raw_rad_f = float(st.get("radius"))
            except (TypeError, ValueError): raw_rad_f = None
            is_blackhole = (raw_mass == "infinity") or (raw_rad_f == SENT)

            if is_blackhole:
                star_w.row([sname, round(ra,7), round(dec,7), round(dist,5), "",
                                 "X", BLACKHOLE_MASS_SOL, "", ""])
                n_bh += 1
            else:
                mass = jf(st.get("mass")); msol = mass/MSUN if (teff and mass and mass > 1e25) else None
                rad  = jf(st.get("radius")); rsol = rad/RSUN if (teff and rad and 1e6 < rad < SENT) else None
                lum  = jf(st.get("luminosity"))
                appmag = ""
                if lum and lum > 0 and dist > 0:
                    appmag = round(4.83 - 2.5*math.log10(lum/LSUN) + 5*math.log10(dist/10.0), 4)
                star_w.row([sname, round(ra,7), round(dec,7), round(dist,5), appmag,
                                 spec, round(msol,5) if msol else "",
                                 round(rsol,5) if rsol else "", round(teff,2) if teff else ""])
            n_sys += 1

            planets = s.get("planets") or {}
            if not planets:
                continue
            out = [f"// ===== {sname} ({sid}) ====="]
            # order by celestialIndex
            for pid, p in sorted(planets.items(),
                                 key=lambda kv: int(jf((kv[1] or {}).get("celestialIndex")) or 0)):
                p = p or {}
                ps   = p.get("statistics", {}) or {}
                ppos = vec(p.get("position"))                 # relative to star
                idx  = int(jf(p.get("celestialIndex")) or 0)
                pname = f"{sname} {roman(idx)}"
                tid  = int(jf(p.get("typeID")) or 0)
                pr_m = jf(p.get("radius")) or jf(ps.get("radius"))
                note = (ps.get("typeDescription") or TYPE_NAME.get(tid, "?")).strip()
                cls  = choose_class(tid, pr_m, note)
                orbit = kepler_orbit(ps, ppos)
                body_block(out, "Planet", pname, sname, cls, ps, pr_m, orbit,
                           args.planet_refplane, f"type {tid} | {note}",
                           args.variety, args.ring_chance, args.life_chance)
                n_pl += 1
                for j, (mid, m) in enumerate(sorted((p.get("moons") or {}).items()), 1):
                    m = m or {}
                    ms = m.get("statistics", {}) or {}
                    mpos = vec(m.get("position"))             # relative to star
                    rel  = tuple(mpos[k]-ppos[k] for k in range(3)) if (mpos and ppos) else None
                    morb = kepler_orbit(ms, rel)
                    mr_m = jf(m.get("radius")) or jf(ms.get("radius"))
                    mnote = (ms.get("typeDescription") or "Moon").strip() or "Moon"
                    body_block(out, "Moon", f"{pname} {chr(96+j)}", pname, "Selena", ms,
                               mr_m, morb, args.moon_refplane, f"type 14 | {mnote}",
                               args.variety, args.ring_chance, args.life_chance)
                    n_mn += 1
            out.append("")
            sc_w.system("\n".join(out) + "\n")

    star_w.close(); sc_w.close()

    if args.nebula_map and zone_points_map:
        res = write_nebula_blobs(args.outdir, zone_points_map, args.field_cell_pc,
                                 args.field_max_blobs, args.field_mag, args.field_radius_scale,
                                 "eve_frontier_nebulae",
                                 "EVE Frontier nebulae -- EXACT in-game per-system mapping.")
        if res:
            print(f"  -> EXACT nebula map: {res[0]} coloured blobs across {res[1]} types "
                  f"(catalogs/nebulae/eve_frontier_nebulae.sc + models/nebulae/eve_frontier_nebulae.cfg)")

    if args.nebula_field and neb_pts:
        res = write_nebula_blobs(args.outdir, radial_zone_points(neb_pts), args.field_cell_pc,
                                 args.field_max_blobs, args.field_mag, args.field_radius_scale,
                                 "eve_frontier_field",
                                 "EVE Frontier nebula field -- spectral approximation of the in-game map.")
        if res:
            print(f"  -> nebula field (approx): {res[0]} coloured blobs across {res[1]} spectral zones "
                  f"(catalogs/nebulae/eve_frontier_field.sc + models/nebulae/eve_frontier_field.cfg)")

    if args.nebula and neb_pts:
        n = len(neb_pts)
        cx = sum(p[0] for p in neb_pts)/n
        cy = sum(p[1] for p in neb_pts)/n
        cz = sum(p[2] for p in neb_pts)/n
        dists = sorted(math.sqrt((p[0]-cx)**2+(p[1]-cy)**2+(p[2]-cz)**2) for p in neb_pts)
        r90 = dists[min(len(dists)-1, int(0.90*len(dists)))]      # 90th pct, ignore outliers
        rad_pc = (r90/PC) * args.nebula_scale
        nra, ndec, ndist = galactic_to_radecdist(cx, cy, cz)
        ndir = os.path.join(args.outdir, "catalogs", "nebulae")
        os.makedirs(ndir, exist_ok=True)
        with open(os.path.join(ndir, "eve_frontier_nebula.sc"), "w", encoding="utf-8") as fh:
            fh.write("// EVE Frontier nebula -- auto-centred on the star cluster.\n")
            fh.write('Nebula "EVE Frontier"\n{\n')
            fh.write('    Galaxy  "Milky Way"\n')
            fh.write('    Type    "Diffuse"\n')
            fh.write(f'    RA      {nra:.7f}\n')
            fh.write(f'    Dec     {ndec:.7f}\n')
            fh.write(f'    Dist    {ndist:.5f}\n')
            fh.write(f'    Radius  {rad_pc:.5f}\n')
            fh.write(f'    AppMagn {args.nebula_mag:g}\n')
            fh.write('}\n')
        print(f"  -> nebula: catalogs/nebulae/eve_frontier_nebula.sc "
              f"(RA {nra:.4f}h Dec {ndec:.4f} Dist {ndist:.1f}pc Radius {rad_pc:.1f}pc)")

    print(f"systems {n_sys}  (black holes {n_bh})  planets {n_pl}  moons {n_mn}")
    print(f"  -> {len(star_w.files)} star file(s): {os.path.basename(star_w.files[0]) if star_w.files else '-'} ...")
    print(f"  -> {len(sc_w.files)} planet file(s): {os.path.basename(sc_w.files[0]) if sc_w.files else '-'} ...")


if __name__ == "__main__":
    main()

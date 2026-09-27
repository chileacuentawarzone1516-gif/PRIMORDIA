#!/usr/bin/env python3
"""
PRIMORDIA · Extractor de geografía
==================================

Convierte un mapa de referencia 2D de "The Island" (vista cenital, océano azul)
en datos vectoriales compactos (anillos lon/lat) y los escribe dentro del
bloque GEO-DATA del script de Blender `blender/primordia_island.py`.

El script de Blender NO depende de esta herramienta ni de la imagen: los datos
quedan embebidos. Esta herramienta solo sirve para regenerarlos.

Uso:
    pip install -r tools/requirements.txt
    python tools/extract_geography.py referencia.webp                 # imprime el bloque
    python tools/extract_geography.py referencia.webp --write blender/primordia_island.py

Proyección: la imagen cubre lat/lon 7.2 → 92.8 (bordes del mapa in-game de
The Island). Validado con el Obelisco Verde (lat 59.2, lon 72.4), que cae sobre
la plataforma circular visible en la imagen.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from skimage import measure

MAP_MIN, MAP_MAX = 7.2, 92.8          # extensión lat/lon que cubre la imagen
BLOCK_BEGIN = "# >>> GEO-DATA (generado por tools/extract_geography.py — no editar a mano)"
BLOCK_END = "# <<< GEO-DATA"


# ─────────────────────────────── Clasificación ───────────────────────────────
def load_rgb(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB")).astype(np.float32)


def classify(img: np.ndarray) -> dict[str, np.ndarray]:
    """Máscaras booleanas por bioma a partir de heurísticas de color del mapa."""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    hi, lo = img.max(-1), img.min(-1)
    sat = (hi - lo) / (hi + 1.0)
    height, width = r.shape
    rows = np.arange(height)[:, None]
    cols = np.arange(width)[None, :]

    ocean = (b > r + 60) & (b > g + 25)
    # Los ríos se dibujan en un azul más apagado y de 2-4 px de ancho.
    river = ~ocean & (b > r + 30) & (b > g - 5) & (lo < 150) & (sat > 0.2)
    land = ~(ocean | river)
    white = land & (lo > 175) & (sat < 0.16)
    ice = land & (b > r + 20) & (g > r + 20) & (lo > 140)
    red = land & (r > g + 3) & (r > b + 35) & ~((r > 170) & (g > 160))
    teal = land & (b > r + 10) & (g > r + 30) & (b > 70) & (g < 175)
    # El turquesa también aparece como franja de aguas someras junto a la costa:
    # solo cuenta como pantano lejos del océano.
    teal &= ~ndi.binary_dilation(ocean, structure=disk(5))
    snow_zone = (rows < height * 0.40) & (cols < width * 0.66)

    return {
        "ocean": ocean,
        "river": river,
        "snow": (white | ice) & snow_zone,
        "redwood": red,
        "swamp": teal,
    }


# ─────────────────────────────── Morfología ──────────────────────────────────
def drop_small(mask: np.ndarray, min_px: int) -> np.ndarray:
    labels, count = ndi.label(mask)
    if count == 0:
        return mask
    sizes = ndi.sum(mask, labels, range(1, count + 1))
    return np.isin(labels, 1 + np.flatnonzero(sizes >= min_px))


def disk(radius: int) -> np.ndarray:
    y, x = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return x * x + y * y <= radius * radius


def region(mask: np.ndarray, close_r: int, min_px: int, sigma: float) -> np.ndarray:
    """Región de bioma compacta: cierre morfológico, relleno y suavizado."""
    m = ndi.binary_closing(mask, structure=disk(close_r))
    m = ndi.binary_fill_holes(drop_small(m, min_px))
    return ndi.gaussian_filter(m.astype(np.float32), sigma)


def coast_field(ocean: np.ndarray, river: np.ndarray) -> np.ndarray:
    """Campo suave tierra(1)/agua(0) conservando ríos y lagos pequeños.

    Los ríos se ensanchan 1 px por lado para que sigan siendo legibles en una
    malla de ~0.2 unidades por celda (un río de 2 px mide solo 0.17 unidades).
    """
    water = ocean | ndi.binary_dilation(river, structure=ndi.generate_binary_structure(2, 2))
    water = drop_small(water, 30)                 # motas azules aisladas
    land = drop_small(~water, 40)                 # motas de tierra
    return ndi.gaussian_filter(land.astype(np.float32), 1.0)


# ─────────────────────────────── Vectorización ───────────────────────────────
def rings_from_field(field: np.ndarray, tolerance: float, min_points: int = 4) -> list[np.ndarray]:
    size = field.shape[0]
    padded = np.pad(field, 1)                     # cierra contornos en el borde
    rings = []
    for contour in measure.find_contours(padded, 0.5):
        poly = measure.approximate_polygon(contour, tolerance)
        if len(poly) < min_points:
            continue
        rows, cols = poly[:, 0] - 1.0, poly[:, 1] - 1.0
        lon = MAP_MIN + (cols + 0.5) / size * (MAP_MAX - MAP_MIN)
        lat = MAP_MIN + (rows + 0.5) / size * (MAP_MAX - MAP_MIN)
        ring = np.stack([lon, lat], axis=1)
        if np.allclose(ring[0], ring[-1]):
            ring = ring[:-1]
        rings.append(ring)
    return rings


def format_rings(name: str, rings: list[np.ndarray], per_line: int = 8) -> str:
    out = [f"{name} = ("]
    for ring in rings:
        pairs = [f"{lon:.2f},{lat:.2f}" for lon, lat in ring]
        chunks = [" ".join(pairs[i:i + per_line]) for i in range(0, len(pairs), per_line)]
        out.append("    (")
        out.extend(f'        "{c} "' for c in chunks)
        out.append("    ),")
    out.append(")")
    return "\n".join(out)


def build_block(image_path: Path) -> str:
    img = load_rgb(image_path)
    if img.shape[0] != img.shape[1]:
        raise SystemExit("La imagen de referencia debe ser cuadrada (vista cenital completa).")
    masks = classify(img)
    scale = img.shape[0] / 1024.0

    area = scale * scale
    coast = rings_from_field(coast_field(masks["ocean"], masks["river"]), tolerance=1.0 * scale)
    snow = rings_from_field(region(masks["snow"], round(8 * scale), int(5000 * area), 4 * scale), 2.0 * scale)
    redwood = rings_from_field(region(masks["redwood"], round(12 * scale), int(3000 * area), 5 * scale), 2.0 * scale)
    swamp = rings_from_field(region(masks["swamp"], round(10 * scale), int(1500 * area), 4 * scale), 2.0 * scale)

    total = sum(len(r) for r in coast + snow + redwood + swamp)
    header = (
        f"# Fuente: {image_path.name} · {len(coast)} anillos de costa · {total} vértices en total.\n"
        "# Formato: cada anillo es una cadena 'lon,lat lon,lat …' (grados de mapa ARK 0‒100).\n"
        "# Los anillos de costa se rellenan con la regla par-impar: islas, lagos y ríos incluidos."
    )
    sections = [
        header,
        format_rings("COAST_RINGS", coast),
        format_rings("SNOW_REGION_RINGS", snow),
        format_rings("REDWOOD_REGION_RINGS", redwood),
        format_rings("SWAMP_REGION_RINGS", swamp),
    ]
    return "\n".join([BLOCK_BEGIN, *sections, BLOCK_END])


def write_into(script: Path, block: str) -> None:
    source = script.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(BLOCK_BEGIN) + r".*?" + re.escape(BLOCK_END), re.S)
    if not pattern.search(source):
        raise SystemExit(f"No se encontró el bloque GEO-DATA en {script}")
    script.write_text(pattern.sub(lambda _: block, source, count=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path, help="Mapa de referencia cenital (PNG/JPG/WEBP, cuadrado)")
    parser.add_argument("--write", type=Path, help="Script de Blender cuyo bloque GEO-DATA se reemplaza")
    args = parser.parse_args(argv)

    block = build_block(args.image)
    if args.write:
        write_into(args.write, block)
        print(f"GEO-DATA actualizado en {args.write}", file=sys.stderr)
    else:
        print(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

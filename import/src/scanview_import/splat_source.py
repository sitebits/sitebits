"""3DGS-PLY/SPLAT lesen; einheitliche aktivierte Gaussians, Quaternions xyzw.

SH Grad 0-3 bleibt erhalten, in coefficient-major RGB-Reihenfolge.
Die Quelle bleibt unverändert. Binär-PLY liefert lokal relativ zum Paketursprung.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.special import expit, logit

from .point_source import read_ply_header

MAX_SPLATS = 10_000_000
MAX_SOURCE_BYTES = 4 * 1024 * 1024 * 1024
C0 = 0.28209479177387814


@dataclass(frozen=True)
class Gaussians:
    centers: np.ndarray
    scales: np.ndarray
    quaternions: np.ndarray
    opacities: np.ndarray
    colors: np.ndarray
    sh_degree: int = 0
    sh_rest: np.ndarray | None = None

    def validate(self) -> None:
        n = len(self.centers)
        if self.sh_degree not in (0, 1, 2, 3):
            raise ValueError("SH-Grad muss 0 bis 3 sein")
        if self.sh_rest is None:
            if self.sh_degree:
                raise ValueError("SH-Koeffizienten fehlen")
        elif (self.sh_rest.shape != (n, (self.sh_degree + 1) ** 2 - 1, 3)
              or not np.isfinite(self.sh_rest).all()):
            raise ValueError("SH-Koeffizienten unvollständig oder nicht endlich")
        if not 0 < n <= MAX_SPLATS:
            raise ValueError(f"Gaussian-Anzahl muss 1 bis {MAX_SPLATS} sein")
        for a, shape in (
            (self.centers, (n, 3)),
            (self.scales, (n, 3)),
            (self.quaternions, (n, 4)),
            (self.opacities, (n,)),
            (self.colors, (n, 3)),
        ):
            if a.shape != shape or not np.isfinite(a).all():
                raise ValueError("Gaussian-Felder unvollständig oder nicht endlich")
        if (self.scales <= 0).any() or ((self.opacities < 0) | (self.opacities > 1)).any():
            raise ValueError("Gaussian-Skalen/Opacity ungültig")
        if (np.linalg.norm(self.quaternions, axis=1) < 1e-8).any():
            raise ValueError("Gaussian-Quaternion ist Null")


def read_gaussians(path: Path) -> Gaussians:
    if not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
        raise ValueError("Splat-Quelle leer oder über 512 MiB")
    if path.suffix.lower() == ".spz":
        from .splat_spz import read_spz

        out = read_spz(path)
    elif path.suffix.lower() == ".splat":
        dtype = np.dtype(
            [("p", "<f4", (3,)), ("s", "<f4", (3,)), ("c", "u1", (4,)), ("q", "u1", (4,))]
        )
        if path.stat().st_size % 32 or path.stat().st_size // 32 > MAX_SPLATS:
            raise ValueError("SPLAT: ungültige Länge/Anzahl")
        r = np.fromfile(path, dtype=dtype)
        q = (r["q"].astype(float) - 128) / 128
        out = Gaussians(
            r["p"].astype(float),
            r["s"].astype(float),
            q[:, [1, 2, 3, 0]],
            r["c"][:, 3].astype(float) / 255,
            r["c"][:, :3].astype(float) / 255,
        )
    elif path.suffix.lower() == ".ply":
        h = read_ply_header(path)
        if not 0 < h.count <= MAX_SPLATS:
            raise ValueError("PLY: Gaussian-Anzahl außerhalb Budget")
        names = [p[0] for p in h.properties]
        required = [
            "x",
            "y",
            "z",
            "opacity",
            *[f"scale_{i}" for i in range(3)],
            *[f"rot_{i}" for i in range(4)],
            *[f"f_dc_{i}" for i in range(3)],
        ]
        if not set(required) <= set(names):
            raise ValueError("PLY: keine vollständigen 3DGS-Gaussians")
        if h.encoding == "ascii":
            with path.open("rb") as f:
                f.seek(h.offset)
                rows = np.loadtxt(f, ndmin=2)
            if rows.shape != (h.count, len(names)):
                raise ValueError("PLY: Länge widerspricht Header")
            fields = {name: rows[:, i] for i, name in enumerate(names)}
        else:
            if path.stat().st_size != h.offset + h.count * h.dtype().itemsize:
                raise ValueError("PLY: Länge widerspricht Header / weitere Elemente")
            rec = np.fromfile(path, dtype=h.dtype(), offset=h.offset, count=h.count)
            fields = {name: rec[name].astype(float) for name in names}

        def cols(keys: list[str]) -> np.ndarray:
            return np.column_stack([fields[k] for k in keys])

        rest = sum(name.startswith("f_rest_") for name in names)
        if rest not in (0, 9, 24, 45):
            raise ValueError("PLY: SH-Grad ungültig")
        out = Gaussians(
            cols(["x", "y", "z"]),
            np.exp(cols([f"scale_{i}" for i in range(3)])),
            cols(["rot_1", "rot_2", "rot_3", "rot_0"]),
            expit(fields["opacity"]),
            C0 * cols([f"f_dc_{i}" for i in range(3)]) + 0.5,
            (0, 9, 24, 45).index(rest),
            cols([f"f_rest_{i}" for i in range(rest)]).reshape(h.count, 3, rest // 3)
            .transpose(0, 2, 1) if rest else np.empty((h.count, 0, 3)),
        )
    else:
        raise ValueError("Splat-Format muss .ply, .spz oder .splat sein")
    out.validate()
    return out


def write_gaussians(path: Path, g: Gaussians, origin: np.ndarray) -> None:
    g.validate()
    names = [
        "x",
        "y",
        "z",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        *[f"f_rest_{i}" for i in range(3 * ((g.sh_degree + 1) ** 2 - 1))],
        "opacity",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    ]
    q = g.quaternions / np.linalg.norm(g.quaternions, axis=1)[:, None]
    rows = np.column_stack(
        [
            g.centers - origin,
            (g.colors - 0.5) / C0,
            (g.sh_rest.transpose(0, 2, 1).reshape(len(g.centers), -1)
             if g.sh_rest is not None and g.sh_degree else np.empty((len(g.centers), 0))),
            logit(np.clip(g.opacities, 1e-7, 1 - 1e-7)),
            np.log(g.scales),
            q[:, [3, 0, 1, 2]],
        ]
    ).astype("<f4")
    if not np.isfinite(rows).all():
        raise ValueError("PLY: Ausgabe übersteigt Float32")
    with path.open("wb") as f:
        f.write(
            (
                "ply\nformat binary_little_endian 1.0\nelement vertex "
                + str(len(rows))
                + "\n"
                + "".join(f"property float {name}\n" for name in names)
                + "end_header\n"
            ).encode()
        )
        f.write(rows.tobytes())

"""Reproducible synthetic RAD import benchmark; output must be new and outside the repo.

No frame bypass: uses a matching 2-million-point synthetic reference cloud and both audits.
Small spacing with proportionate Gaussian scales keeps the cloud sampling error below the gate.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import laspy
import numpy as np
from generate_large_splats import generate

from scanview_import.splat_convert import add_splats


def benchmark(root: Path, tool: Path | None = None, count: int = 10_000_000) -> dict[str, Any]:
    if root.exists() or root.resolve().is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError('Output must be new and outside the repository')
    if not 10_000 <= count <= 10_000_000:
        raise ValueError('Count must be 10000..10000000')
    root.mkdir(parents=True)
    source = root / 'synthetic.spz'
    generated = generate(source, count, 0.001)
    pkg = root / 'pkg'
    pkg.mkdir()
    ix = np.arange(0, count, 5, dtype=np.uint32)
    xyz = np.column_stack([((ix % 1000).astype(float) - 500) * .001,
                           (((ix // 1000) % 1000).astype(float) - 500) * .001,
                           (ix // 1_000_000).astype(float)])
    header = laspy.LasHeader(point_format=3, version='1.2')
    header.scales = [.001] * 3
    las = laspy.LasData(header)
    las.x, las.y, las.z = xyz.T
    las.write(pkg / 'cloud.las')
    del ix, xyz, las
    (pkg / 'project.json').write_text(json.dumps({
        'schemaVersion': 2, 'projectId': 'fixture', 'revision': 1,
        'frame': {'unit': 'm', 'originWorld': [0, 0, 0],
                  'crs': {'wkt': None, 'verticalDatum': None}},
        'layers': [{'id': 'punkte', 'kind': 'pointcloud', 'url': 'cloud.las'}],
    }))
    start = time.monotonic()
    result = add_splats(source, pkg, same_frame=True, initial=True,
                        output_format='rad', build_lod=tool)
    elapsed = time.monotonic() - start
    layer = result['layers'][-1]
    report = json.loads((pkg / layer['verify']['report']).read_text())
    return {'generated': generated, 'importSeconds': elapsed, 'layer': layer, 'report': report}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--build-lod', type=Path)
    parser.add_argument('--count', type=int, default=10_000_000)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.output, args.build_lod, args.count)), flush=True)

"""Unkomprimierte GLB-Inhalte mit lokalen Float-Positionen und eingebetteten PNG-Texturen."""

from pathlib import Path

import numpy as np
import pygltflib as g

from ..mesh_gltf import save_glb_document
from ..mesh_source import Mesh


def rundung(positions: np.ndarray, origin: np.ndarray) -> float:
    """Derselbe Float32-Rückweg wie beim Schreiben, ohne Texturarbeit."""
    local = positions - origin
    pos = np.ascontiguousarray(local[:, [0, 2, 1]] * [1, 1, -1], dtype="<f4")
    if float(np.spacing(np.float32(np.abs(pos).max()))) > 0.00025:
        raise ValueError("Kachel räumlich zu groß für die Float-Positionsgenauigkeit")
    return float(
        np.linalg.norm(local - pos.astype(np.float64)[:, [0, 2, 1]] * [1, -1, 1], axis=1).max()
    )


def schreiben(
    mesh: Mesh,
    path: Path,
    origin: np.ndarray,
    textures: dict[int, bytes],
    node_origin: bool = False,
) -> float:
    chunks: list[bytes] = []
    views: list[g.BufferView] = []
    accessors: list[g.Accessor] = []

    def attribute(data: np.ndarray, kind: str, component: int, target: int) -> int:
        payload = data.tobytes()
        views.append(
            g.BufferView(
                buffer=0, byteOffset=sum(map(len, chunks)), byteLength=len(payload), target=target
            )
        )
        chunks.append(payload + b"\0" * (-len(payload) % 4))
        accessors.append(
            g.Accessor(
                bufferView=len(views) - 1,
                componentType=component,
                count=len(data),
                type=kind,
                min=data.min(axis=0).reshape(-1).tolist(),
                max=data.max(axis=0).reshape(-1).tolist(),
            )
        )
        return len(accessors) - 1

    local = mesh.positions - origin
    pos = np.ascontiguousarray(local[:, [0, 2, 1]] * [1, 1, -1], dtype="<f4")
    step = float(np.spacing(np.float32(np.abs(pos).max())))
    if step > 0.00025:
        raise ValueError("Kachel räumlich zu groß für die Float-Positionsgenauigkeit")
    attrs = g.Attributes(POSITION=attribute(pos, "VEC3", g.FLOAT, g.ARRAY_BUFFER))
    if mesh.uv is not None:
        attrs.TEXCOORD_0 = attribute(
            np.asarray(mesh.uv, dtype="<f4"), "VEC2", g.FLOAT, g.ARRAY_BUFFER
        )
    if mesh.colors is not None and not all(int(m) in textures for m in np.unique(mesh.materials)):
        attrs.COLOR_0 = attribute(
            np.asarray(mesh.colors, dtype="<f4"), "VEC3", g.FLOAT, g.ARRAY_BUFFER
        )
    images, texs, mats, primitives = [], [], [], []
    for material in np.unique(mesh.materials):
        info = None
        if int(material) in textures:
            payload = textures[int(material)]
            views.append(
                g.BufferView(buffer=0, byteOffset=sum(map(len, chunks)), byteLength=len(payload))
            )
            chunks.append(payload + b"\0" * (-len(payload) % 4))
            images.append(g.Image(bufferView=len(views) - 1, mimeType="image/png"))
            texs.append(g.Texture(source=len(images) - 1, sampler=0))
            info = g.TextureInfo(index=len(texs) - 1)
        mats.append(
            g.Material(
                pbrMetallicRoughness=g.PbrMetallicRoughness(
                    baseColorTexture=info, metallicFactor=0, roughnessFactor=1
                ),
                doubleSided=True,
                extensions={"KHR_materials_unlit": {}},
            )
        )
        indices = np.asarray(mesh.triangles[mesh.materials == material].reshape(-1), dtype="<u4")
        acc = attribute(indices, "SCALAR", g.UNSIGNED_INT, g.ELEMENT_ARRAY_BUFFER)
        primitives.append(g.Primitive(attributes=attrs, indices=acc, material=len(mats) - 1))
    translation = (origin[[0, 2, 1]] * [1, 1, -1]).tolist() if node_origin else None
    blob = b"".join(chunks)
    gltf = g.GLTF2(
        asset=g.Asset(version="2.0", generator="scanview-import"),
        extensionsUsed=["KHR_materials_unlit"],
        extensionsRequired=["KHR_materials_unlit"],
        scene=0,
        scenes=[g.Scene(nodes=[0])],
        nodes=[g.Node(mesh=0, translation=translation)],
        meshes=[g.Mesh(primitives=primitives)],
        buffers=[g.Buffer(byteLength=len(blob))],
        bufferViews=views,
        accessors=accessors,
        images=images,
        textures=texs,
        materials=mats,
        samplers=[g.Sampler(wrapS=10497, wrapT=10497)],
    )
    save_glb_document(gltf, blob, path)
    return rundung(mesh.positions, origin)

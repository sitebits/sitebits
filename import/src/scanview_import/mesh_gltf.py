"""glTF lesen; Node-Transformationen anwenden, keine Netzressourcen laden."""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any, cast

import meshoptimizer
import numpy as np
import pygltflib as g
from scipy.spatial.transform import Rotation

from .mesh_quantization import quantize_positions
from .mesh_source import Mesh
from .mesh_stages import degenerate_mask, optimize_layout
from .point_source import PointSource


def resource_bytes(uri: str, parent: Path) -> bytes:
    if uri.startswith("data:"):
        if ";base64," not in uri:
            raise ValueError("glTF: nur Base64-Daten-URI unterstützt")
        return base64.b64decode(uri.split(",", 1)[1], validate=True)
    if ":" in uri or uri.startswith("/"):
        raise ValueError("glTF: nur relative lokale Ressourcen unterstützt")
    p = parent / uri
    if not p.is_file():
        raise ValueError(f"glTF-Ressource fehlt: {uri}")
    return p.read_bytes()


def read_gltf(source: PointSource) -> Mesh:
    gltf = g.GLTF2().load(str(source.path))
    if set(gltf.extensionsRequired or []) - {
        "KHR_materials_unlit",
        "KHR_texture_basisu",
        "EXT_meshopt_compression",
        "KHR_mesh_quantization",
    }:
        raise ValueError(f"glTF: nicht unterstützte Erweiterungen {gltf.extensionsRequired}")
    buffers = [
        resource_bytes(b.uri, source.path.parent) if b.uri else gltf.binary_blob()
        for b in gltf.buffers
    ]

    decoded: dict[int, bytes] = {}

    def view(index: int) -> bytes:
        if index in decoded:
            return decoded[index]
        bv = gltf.bufferViews[index]
        ext = (bv.extensions or {}).get("EXT_meshopt_compression")
        if ext is None:
            data = buffers[bv.buffer][(bv.byteOffset or 0) : (bv.byteOffset or 0) + bv.byteLength]
        else:
            count, stride = ext["count"], ext["byteStride"]
            offset, size = ext.get("byteOffset", 0), ext["byteLength"]
            if count <= 0 or stride * count != bv.byteLength or offset < 0 or size <= 0:
                raise ValueError("meshopt: ungültige Pufferlänge")
            if ext.get("filter", "NONE") != "NONE":
                raise ValueError("meshopt: Filter nicht unterstützt")
            buf = buffers[ext["buffer"]]
            if offset + size > len(buf):
                raise ValueError("meshopt: komprimierter Puffer abgeschnitten")
            payload = buf[offset : offset + size]
            if ext["mode"] == "ATTRIBUTES":
                data = meshoptimizer.decode_vertex_buffer(count, stride, payload).tobytes()
            elif ext["mode"] == "TRIANGLES" and stride == 4:
                data = meshoptimizer.decode_index_buffer(count, stride, payload).tobytes()
            else:
                raise ValueError("meshopt: Modus nicht unterstützt")
        decoded[index] = data
        return cast(bytes, data)

    def accessor(index: int) -> np.ndarray:
        a = gltf.accessors[index]
        if a.sparse is not None or a.bufferView is None:
            raise ValueError("glTF: Sparse-/leerer Accessor nicht unterstützt")
        typ = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}[
            a.componentType
        ]
        components = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a.type]
        dtype = np.dtype(typ)
        bv = gltf.bufferViews[a.bufferView]
        stride = bv.byteStride or dtype.itemsize * components
        result = np.ndarray(
            (a.count, components),
            dtype=dtype,
            buffer=view(a.bufferView),
            offset=a.byteOffset or 0,
            strides=(stride, dtype.itemsize),
        ).copy()
        if a.normalized:
            if dtype.kind not in "iu":
                raise ValueError("glTF: normalisierter Float-Accessor")
            result = np.maximum(
                result.astype(np.float64) / np.iinfo(dtype).max, -1 if dtype.kind == "i" else 0
            )
        return result

    images = [
        resource_bytes(im.uri, source.path.parent) if im.uri else view(im.bufferView)
        for im in gltf.images
    ]
    textures: list[Path | bytes | None] = []
    for mat in gltf.materials:
        tex = mat.pbrMetallicRoughness.baseColorTexture if mat.pbrMetallicRoughness else None
        if tex is None:
            textures.append(None)
        else:
            if tex.texCoord not in (None, 0) or tex.extensions:
                raise ValueError("glTF: nur untransformierte TEXCOORD_0 unterstützt")
            t = gltf.textures[tex.index]
            image_id = (t.extensions or {}).get("KHR_texture_basisu", {}).get("source", t.source)
            textures.append(images[image_id])
    default_material = len(textures)
    textures.append(None)
    positions: list[np.ndarray] = []
    triangles: list[np.ndarray] = []
    uvlist: list[np.ndarray | None] = []
    colorlist: list[np.ndarray | None] = []
    materials: list[np.ndarray] = []
    count = 0
    shared: dict[tuple[bytes, int, int | None, int | None, bytes], int] = {}

    def color_factor(material: int | None) -> np.ndarray:
        """baseColorFactor des Materials (RGBA); nur deckende Materialien."""
        if material is None:
            return np.ones(4)
        mat = gltf.materials[material]
        pbr = mat.pbrMetallicRoughness
        factor = np.asarray(pbr.baseColorFactor or [1, 1, 1, 1]) if pbr else np.ones(4)
        if mat.alphaMode not in (None, "OPAQUE") or factor[3] != 1:
            raise ValueError("glTF: transparente Materialien nicht unterstützt")
        return factor

    def visit(index: int, parent: np.ndarray, ancestors: set[int]) -> None:
        nonlocal count
        if index in ancestors:
            raise ValueError("glTF: zyklischer Node-Baum")
        node = gltf.nodes[index]
        if node.skin is not None:
            raise ValueError("glTF: Skinning nicht unterstützt")
        if node.matrix:
            local = np.array(node.matrix, dtype=np.float64).reshape(4, 4).T
        else:
            local = np.eye(4)
            local[:3, :3] = Rotation.from_quat(node.rotation or [0, 0, 0, 1]).as_matrix()
            local[:3, :3] *= np.asarray(node.scale or [1, 1, 1])[None, :]
            local[:3, 3] = node.translation or [0, 0, 0]
        transform = parent @ local
        if node.mesh is not None:
            for prim in gltf.meshes[node.mesh].primitives:
                if prim.mode not in (None, 4) or prim.targets:
                    raise ValueError("glTF: nur statische Dreiecke unterstützt")
                attrs = prim.attributes
                vertex_count = gltf.accessors[attrs.POSITION].count
                idx = (
                    accessor(prim.indices).ravel()
                    if prim.indices is not None
                    else np.arange(vertex_count)
                )
                if len(idx) and (idx.min() < 0 or idx.max() >= vertex_count):
                    raise ValueError("glTF: Index außerhalb des Vertexpuffers")
                if len(idx) % 3:
                    raise ValueError("glTF: unvollständige Dreiecke")
                faces = idx.reshape(-1, 3).astype(np.uint32)
                if np.linalg.det(transform[:3, :3]) < 0:
                    faces = faces[:, [0, 2, 1]]
                material = prim.material if prim.material is not None else default_material
                factor = color_factor(prim.material)
                if textures[material] is not None and attrs.TEXCOORD_0 is None:
                    raise ValueError("glTF: Textur ohne UV")
                # Primitive, die sich Vertexattribute teilen (scanview schreibt je Material ein
                # Primitive auf EINEN Vertexpuffer), teilen sich auch hier die Vertices. Sonst
                # entstünde je Material eine volle Kopie: 20 Materialien = 20-facher Speicher,
                # und Nähte zwischen Materialien zerfielen in getrennte Ränder.
                key = (
                    transform.tobytes(),
                    attrs.POSITION,
                    attrs.TEXCOORD_0,
                    attrs.COLOR_0,
                    factor[:3].tobytes(),
                )
                base = shared.get(key)
                if base is None:
                    base = shared[key] = count
                    xyz = accessor(attrs.POSITION).astype(np.float64)
                    positions.append(xyz @ transform[:3, :3].T + transform[:3, 3])
                    uvlist.append(
                        accessor(attrs.TEXCOORD_0) if attrs.TEXCOORD_0 is not None else None
                    )
                    color = accessor(attrs.COLOR_0) if attrs.COLOR_0 is not None else None
                    if not np.all(factor[:3] == 1):
                        color = (
                            color[:, :3] if color is not None else np.ones((vertex_count, 3))
                        ) * factor[:3]
                    colorlist.append(color)
                    count += vertex_count
                triangles.append(faces + base)
                materials.append(np.full(len(faces), material, dtype=np.int32))
        for child in node.children or []:
            visit(child, transform, ancestors | {index})

    if gltf.animations:
        raise ValueError("glTF: Animationen nicht unterstützt")
    roots = gltf.scenes[gltf.scene or 0].nodes if gltf.scenes else []
    for root in roots:
        visit(root, np.eye(4), set())
    if not positions:
        raise ValueError("glTF ohne Mesh")
    uv = (
        np.concatenate(
            [
                a if a is not None else np.zeros((len(p), 2))
                for a, p in zip(uvlist, positions, strict=True)
            ]
        )
        if any(a is not None for a in uvlist)
        else None
    )
    colors = (
        np.concatenate(
            [
                a[:, :3] if a is not None else np.ones((len(p), 3))
                for a, p in zip(colorlist, positions, strict=True)
            ]
        )
        if any(a is not None for a in colorlist)
        else None
    )
    return Mesh(
        source.transform(np.concatenate(positions)),
        np.concatenate(triangles),
        uv,
        colors,
        np.concatenate(materials),
        textures,
        source,
    )


def write_glb(
    mesh: Mesh,
    path: Path,
    origin: np.ndarray,
    encoded: dict[int, bytes],
    removal: dict[str, Any] | None = None,
) -> float:
    """Y oben im lokalen Rahmen; Integerpositionen mit expliziter Dekodiertransformation."""
    mesh = optimize_layout(mesh)
    chunks: list[bytes] = []
    views: list[g.BufferView] = []
    accessors: list[g.Accessor] = []
    fallback_size = 0

    def section(data: bytes, target: int | None = None) -> int:
        offset = sum(len(c) for c in chunks)
        chunks.append(data + b"\x00" * (-len(data) % 4))
        views.append(g.BufferView(buffer=0, byteOffset=offset, byteLength=len(data), target=target))
        return len(views) - 1

    def attribute(
        values: np.ndarray, kind: str, component: int, target: int, normalized: bool = False
    ) -> int:
        nonlocal fallback_size
        count = len(values)
        stride = values.nbytes // count
        mode = "TRIANGLES" if target == g.ELEMENT_ARRAY_BUFFER else "ATTRIBUTES"
        payload = (
            meshoptimizer.encode_index_buffer(values)
            if mode == "TRIANGLES"
            else meshoptimizer.encode_vertex_buffer(values)
        )
        bv = section(payload)
        view = views[bv]
        view.extensions = {
            "EXT_meshopt_compression": {
                "buffer": 0,
                "byteOffset": view.byteOffset,
                "byteLength": len(payload),
                "byteStride": stride,
                "count": count,
                "mode": mode,
                "filter": "NONE",
            }
        }
        view.buffer, view.byteOffset, view.byteLength = 1, fallback_size, values.nbytes
        view.target = target
        if mode == "ATTRIBUTES":
            view.byteStride = stride
        fallback_size += values.nbytes
        a = g.Accessor(
            bufferView=bv, componentType=component, count=count, type=kind, normalized=normalized
        )
        if kind == "VEC3":
            a.min, a.max = values[:, :3].min(axis=0).tolist(), values[:, :3].max(axis=0).tolist()
        accessors.append(a)
        return len(accessors) - 1

    positions, minimum, step, quantized = quantize_positions(mesh.positions, origin)
    if quantized:
        position = attribute(positions, "VEC3", g.UNSIGNED_SHORT, g.ARRAY_BUFFER)
        node = g.Node(mesh=0, translation=minimum.tolist(), scale=[step] * 3)
    else:
        position = attribute(positions, "VEC3", g.FLOAT, g.ARRAY_BUFFER)
        node = g.Node(mesh=0)
    mask, quantization_report = degenerate_mask(positions[:, :3], mesh.triangles)
    if mask.all():
        raise ValueError("Mesh nach Quantisierung vollständig entartet")
    if removal is not None:
        removal.update(quantization_report)
    faces, material_ids = mesh.triangles[~mask], mesh.materials[~mask]
    attrs = g.Attributes(POSITION=position)
    if mesh.uv is not None:
        if np.all((mesh.uv >= 0) & (mesh.uv <= 1)):
            uv = np.ascontiguousarray(np.rint(mesh.uv * 65535), dtype="<u2")
            attrs.TEXCOORD_0 = attribute(uv, "VEC2", g.UNSIGNED_SHORT, g.ARRAY_BUFFER, True)
        else:
            attrs.TEXCOORD_0 = attribute(
                np.asarray(mesh.uv, dtype="<f4"), "VEC2", g.FLOAT, g.ARRAY_BUFFER
            )
    # Sind alle Materialien texturiert, entfallen die Vertexfarben: glTF multipliziert COLOR_0 mit
    # der Textur, und Metashapes Vertexfarben sind nur eine grobe Kopie der Texturfarben — das
    # Produkt dunkelte die Textur doppelt ab. Untexturierte Materialien brauchen sie weiter.
    alle_texturiert = all(int(m) in encoded for m in np.unique(material_ids))
    if mesh.colors is not None and not alle_texturiert:
        colors = np.full((len(positions), 4), 255, dtype=np.uint8)
        colors[:, :3] = np.rint(np.clip(mesh.colors, 0, 1) * 255).astype(np.uint8)
        attrs.COLOR_0 = attribute(colors, "VEC4", g.UNSIGNED_BYTE, g.ARRAY_BUFFER, True)
    images, textures, materials, primitives = [], [], [], []
    for material in np.unique(material_ids):
        texinfo = None
        if int(material) in encoded:
            bv = section(encoded[int(material)])
            images.append(g.Image(bufferView=bv, mimeType="image/ktx2"))
            textures.append(
                g.Texture(sampler=0, extensions={"KHR_texture_basisu": {"source": len(images) - 1}})
            )
            texinfo = g.TextureInfo(index=len(textures) - 1)
        materials.append(
            g.Material(
                pbrMetallicRoughness=g.PbrMetallicRoughness(
                    baseColorTexture=texinfo, metallicFactor=0, roughnessFactor=1
                ),
                doubleSided=True,
                extensions={"KHR_materials_unlit": {}},
            )
        )
        idx = np.ascontiguousarray(faces[material_ids == material].ravel(), dtype="<u4")
        acc = attribute(idx, "SCALAR", g.UNSIGNED_INT, g.ELEMENT_ARRAY_BUFFER)
        primitives.append(g.Primitive(attributes=attrs, indices=acc, material=len(materials) - 1))
    blob = b"".join(chunks)
    extensions = ["KHR_materials_unlit", "EXT_meshopt_compression"]
    if quantized:
        extensions.append("KHR_mesh_quantization")
    if textures:
        extensions.append("KHR_texture_basisu")
    gltf = g.GLTF2(
        asset=g.Asset(version="2.0", generator="scanview-import"),
        scene=0,
        scenes=[g.Scene(nodes=[0])],
        nodes=[node],
        meshes=[g.Mesh(primitives=primitives)],
        buffers=[
            g.Buffer(byteLength=len(blob)),
            g.Buffer(
                byteLength=fallback_size, extensions={"EXT_meshopt_compression": {"fallback": True}}
            ),
        ],
        bufferViews=views,
        accessors=accessors,
        images=images,
        textures=textures,
        materials=materials,
        samplers=[g.Sampler(wrapS=10497, wrapT=10497)],
        extensionsUsed=extensions,
        extensionsRequired=extensions,
    )
    save_glb_document(gltf, blob, path)
    return step


def save_glb_document(gltf: g.GLTF2, blob: bytes, path: Path) -> None:
    """GLB-Container ohne pygltflib-Pufferflattening (erhält meshopt-Placeholder)."""
    document = gltf.gltf_to_json(separators=(",", ":"), indent=None).encode("utf-8")
    document += b" " * (-len(document) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    path.write_bytes(
        struct.pack("<4sII", b"glTF", 2, 28 + len(document) + len(blob))
        + struct.pack("<I4s", len(document), b"JSON")
        + document
        + struct.pack("<I4s", len(blob), b"BIN\x00")
        + blob
    )

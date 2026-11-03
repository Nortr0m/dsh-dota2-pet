#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dota 2 英雄 3D 渲染器（无第三方依赖，纯 numpy + Pillow）

流程：
    GLB 解析 -> 动画采样 -> 骨骼层级求世界矩阵 -> 线性混合蒙皮(LBS)
    -> 透视投影 -> 三角形光栅化(z-buffer) -> Gouraud 着色 -> 带透明通道 PNG

用法示例：
    python render.py --glb export/slark.glb --anim portrait --t 0.4 \
        --yaw 25 --pitch 8 --size 420 --out gray_slark.png
    python render.py --glb export/slark.glb --bake portrait --frames 24 \
        --yaws 8 --outdir frames3d/slark --size 220
"""

import argparse
import json
import math
import os
import struct
import numpy as np
from PIL import Image

# ---------------------------------------------------------------- GLB 解析

_COMP = {5120: ("i1", 1), 5121: ("u1", 1), 5122: ("i2", 2), 5123: ("u2", 2),
         5125: ("u4", 4), 5126: ("f4", 4)}
_NUM = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


def _accessor(j, blob, idx):
    a = j["accessors"][idx]
    fmt, size = _COMP[a["componentType"]]
    n = _NUM[a["type"]]
    count = a["count"]
    if "bufferView" not in a:
        return np.zeros((count, n), dtype=np.float64)
    bv = j["bufferViews"][a["bufferView"]]
    base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride") or size * n
    dt = np.dtype("<" + fmt)
    if stride == size * n:
        arr = np.frombuffer(blob, dtype=dt, count=count * n, offset=base).reshape(count, n)
    else:
        raw = np.frombuffer(blob, dtype=np.uint8, count=stride * (count - 1) + size * n, offset=base)
        arr = np.stack([raw[i * stride:i * stride + size * n].view(dt).reshape(n) for i in range(count)])
    arr = arr.astype(np.float64)
    if a.get("normalized"):
        mx = {"i1": 127.0, "u1": 255.0, "i2": 32767.0, "u2": 65535.0}.get(fmt)
        if mx:
            arr = np.maximum(arr / mx, -1.0)
    return arr


class Model:
    """一个 GLB 里的可渲染内容。"""

    def __init__(self, path):
        data = open(path, "rb").read()
        magic, ver, length = struct.unpack("<III", data[:12])
        assert magic == 0x46546C67, "不是 GLB 文件"
        off, self.json, self.blob = 12, None, b""
        while off < length:
            clen, ctype = struct.unpack("<II", data[off:off + 8])
            chunk = data[off + 8:off + 8 + clen]
            if ctype == 0x4E4F534A:
                self.json = json.loads(chunk.decode("utf-8"))
            elif ctype == 0x004E4942:
                self.blob = chunk
            off += 8 + clen
        j = self.json
        self.dir = os.path.dirname(os.path.abspath(path))
        self.nodes = j.get("nodes", [])
        self.scene_roots = j.get("scenes", [{}])[j.get("scene", 0)].get("nodes", [])

        # 网格：把所有 primitive 拼成一整块
        pos, nrm, uv, joints, weights, index, mat, tan = [], [], [], [], [], [], [], []
        base = 0
        for mesh in j.get("meshes", []):
            for pr in mesh["primitives"]:
                at = pr["attributes"]
                p = _accessor(j, self.blob, at["POSITION"])
                pos.append(p)
                nrm.append(_accessor(j, self.blob, at["NORMAL"]) if "NORMAL" in at else np.zeros_like(p))
                uv.append(_accessor(j, self.blob, at["TEXCOORD_0"]) if "TEXCOORD_0" in at else np.zeros((len(p), 2)))
                # TANGENT 是 VEC4（xyz + 手性 w），法线贴图要用它建 TBN
                tan.append(_accessor(j, self.blob, at["TANGENT"]) if "TANGENT" in at
                           else np.tile(np.array([1.0, 0.0, 0.0, 1.0]), (len(p), 1)))
                mat.append(np.full(len(p), pr.get("material", -1), dtype=np.int64))
                if "JOINTS_0" in at and "WEIGHTS_0" in at:
                    joints.append(_accessor(j, self.blob, at["JOINTS_0"]).astype(np.int64))
                    w = _accessor(j, self.blob, at["WEIGHTS_0"])
                    s = w.sum(axis=1, keepdims=True)
                    s[s == 0] = 1.0
                    weights.append(w / s)
                else:
                    joints.append(np.zeros((len(p), 4), dtype=np.int64))
                    weights.append(np.zeros((len(p), 4)))
                if "indices" in pr:
                    ix = _accessor(j, self.blob, pr["indices"]).astype(np.int64).ravel() + base
                else:
                    ix = np.arange(len(p), dtype=np.int64) + base
                index.append(ix)
                base += len(p)
        self.POS = np.concatenate(pos) if pos else np.zeros((0, 3))
        self.NRM = np.concatenate(nrm) if nrm else np.zeros((0, 3))
        self.UV = np.concatenate(uv) if uv else np.zeros((0, 2))
        self.TAN = np.concatenate(tan) if tan else np.zeros((0, 4))
        self.JOINTS = np.concatenate(joints).astype(np.int64) if joints else np.zeros((0, 4), np.int64)
        self.WEIGHTS = np.concatenate(weights) if weights else np.zeros((0, 4))
        self.INDEX = np.concatenate(index).astype(np.int64) if index else np.zeros(0, np.int64)
        self.MAT = np.concatenate(mat) if mat else np.full(len(self.POS), -1, np.int64)
        self._load_textures()
        self.TRIS = self.INDEX.reshape(-1, 3)

        # 骨骼
        self.skin_joints = []
        self.ibm = None
        if j.get("skins"):
            sk = j["skins"][0]
            self.skin_joints = np.array(sk.get("joints", []), dtype=np.int64)
            if "inverseBindMatrices" in sk:
                m = _accessor(j, self.blob, sk["inverseBindMatrices"])
                self.ibm = m.reshape(-1, 4, 4).transpose(0, 2, 1)   # 列主序 -> 行主序

        # 动画
        self.anims = {}
        for a in j.get("animations", []):
            self.anims[a.get("name", "?")] = a

    # ---------------------------------------------------------- 姿态求值
    def _node_local(self, i, over):
        nd = self.nodes[i]
        o = over.get(i, {})
        if "matrix" in nd and not o:
            return np.array(nd["matrix"], dtype=np.float64).reshape(4, 4).T
        t = np.array(o.get("translation", nd.get("translation", [0, 0, 0])), dtype=np.float64)
        r = np.array(o.get("rotation", nd.get("rotation", [0, 0, 0, 1])), dtype=np.float64)
        s = np.array(o.get("scale", nd.get("scale", [1, 1, 1])), dtype=np.float64)
        x, y, z, w = r
        n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
        x, y, z, w = x / n, y / n, z / n, w / n
        m = np.eye(4)
        m[:3, :3] = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        m[:3, :3] *= s[None, :]
        m[:3, 3] = t
        return m

    def _world(self, over):
        out = {}
        stack = [(r, np.eye(4)) for r in self.scene_roots]
        while stack:
            i, parent = stack.pop()
            m = parent @ self._node_local(i, over)
            out[i] = m
            for c in self.nodes[i].get("children", []):
                stack.append((c, m))
        return out

    def merge_parts(self, paths, transfer_weights=True, attach=None):
        """把静态部件（默认饰品）并进本体。

        attach：{部件路径 or 槽位名: 骨骼名}，用于**挂在挂点上的部件**（典型是武器）。

        为什么需要它：武器这类部件在源文件里**没有蒙皮数据、顶点还在自己的局部空间**
        （斯拉克武器的顶点 bbox 是 x[-0.46,0.86] y[-0.13,0.13] z[-1.30,0.90]，
        中心就在世界原点），而本体里有一根 weapon_2 骨骼，它在动画中跟着右手移动——
        那就是武器的挂点。不做这一步，武器会静止在角色脚底（实测就是这样）。
        处理方式：先用挂点骨骼的**绑定姿势**把顶点搬到正确位置，再刚性绑到该骨骼上。

        其余部件走权重传递（首选）或最近骨骼刚性绑定（兜底）。
        """
        attach = dict(attach or {})

        def _bind_name(path):
            """在 attach 里找这个部件该挂哪根骨骼：文件名 / 完整路径 / 去后缀名都试。"""
            if not attach:
                return None
            base = os.path.basename(str(path))
            stem = os.path.splitext(base)[0]
            for key in (str(path), base, stem):
                if key in attach:
                    return attach[key]
            # 退化匹配：attach 的键是文件名的一部分（例如 weapon）
            for k, v in attach.items():
                kb = os.path.splitext(os.path.basename(str(k)))[0]
                if kb and (kb in stem or stem.endswith(kb)):
                    return v
            return None

        body_POS = self.POS.copy()
        body_J = self.JOINTS.copy()
        body_W = self.WEIGHTS.copy()
        have_skin = len(body_W) > 0 and float(body_W.sum()) > 0.0

        world = self._world({})
        bnames = [self.nodes[int(n)].get("name") or "" for n in self.skin_joints] if len(self.skin_joints) else []
        if len(self.skin_joints):
            bone_pos = np.array([world[int(n)][:3, 3] for n in self.skin_joints])
        else:
            bone_pos = None
        for path in paths:
            pm = Model(path)
            mats = pm.json.get("materials", [])
            own = self.json.setdefault("materials", [])
            remap = {}
            for mi in range(len(pm.tex)):
                want = mats[mi].get("name") if mi < len(mats) else None
                idx = None
                for i, mt in enumerate(own):
                    if mt.get("name") == want:
                        idx = i
                        break
                if idx is None:
                    own.append(mats[mi] if mi < len(mats) else {"name": want})
                    self.tex.append(pm.tex[mi])
                    idx = len(self.tex) - 1
                remap[mi] = idx
            n = len(pm.POS)
            off = len(self.POS)
            part_pos = pm.POS
            bind = _bind_name(path)
            bind_slot = None
            if bind and bnames:
                for s, nm in enumerate(bnames):
                    if nm == bind:
                        bind_slot = s
                        break
            if bind_slot is not None:
                # 挂在挂点上的部件：**只把权重绑到挂点骨骼，不要预变换顶点**。
                #
                # 踩过的坑：一开始先用挂点骨骼的姿势矩阵把顶点搬过去、再绑骨骼，
                # 结果武器悬浮在肩上方（画面纵向 21%，应约 40%）——因为蒙皮时骨骼
                # 矩阵已经承担了全部变换（weapon_2 的逆绑定矩阵实测就是单位阵），
                # 预变换等于把变换做了两遍。
                # 武器源文件里没有蒙皮数据、顶点本来就在局部空间，正好只差"绑骨骼"这一步。
                j4 = np.stack([np.full(n, bind_slot, np.int64), np.zeros(n, np.int64),
                               np.zeros(n, np.int64), np.zeros(n, np.int64)], axis=1)
                w4 = np.zeros((n, 4))
                w4[:, 0] = 1.0
                part_nrm = pm.NRM
            elif transfer_weights and have_skin:
                # 最近邻权重传递：抄最近本体顶点的骨骼与权重
                d = ((pm.POS[:, None, :] - body_POS[None, :, :]) ** 2).sum(axis=2)
                nn = d.argmin(axis=1)
                j4 = body_J[nn]
                w4 = body_W[nn]
                part_nrm = pm.NRM
            elif bone_pos is not None and len(bone_pos):
                d = ((pm.POS[:, None, :] - bone_pos[None, :, :]) ** 2).sum(axis=2)
                near = d.argmin(axis=1)
                # JOINTS_0 要的是"骨骼在 skin.joints 里的槽位序号"，不是节点 id。
                # bone_pos 是按 skin_joints 顺序建的，所以 argmin 的结果本来就是槽位序号。
                # 这里曾经错写成 skin_joints[near]（节点 id），斯拉克恰好槽位与节点 id 都小于
                # 关节数、没抛异常但**绑错了骨骼**；食人魔节点更多就直接越界崩了。
                j4 = np.stack([near.astype(np.int64), np.zeros(n, np.int64),
                               np.zeros(n, np.int64), np.zeros(n, np.int64)], axis=1)
                w4 = np.zeros((n, 4))
                w4[:, 0] = 1.0
                part_nrm = pm.NRM
            else:
                j4 = np.zeros((n, 4), np.int64)
                w4 = np.zeros((n, 4))
                part_nrm = pm.NRM
            pm_mat = np.array([remap.get(int(m), -1) for m in pm.MAT])
            self.POS = np.vstack([self.POS, part_pos])
            self.NRM = np.vstack([self.NRM, part_nrm])
            self.UV = np.vstack([self.UV, pm.UV])
            self.TAN = np.vstack([self.TAN, pm.TAN])
            self.JOINTS = np.vstack([self.JOINTS, j4])
            self.WEIGHTS = np.vstack([self.WEIGHTS, w4])
            self.MAT = np.concatenate([self.MAT, pm_mat])
            self.TRIS = np.vstack([self.TRIS, pm.TRIS + off])
        self.skin_joints = np.array(self.skin_joints)
        self.INDEX = self.TRIS.ravel()
        return self

    def _load_textures(self):
        """从 GLB 里取出每个材质的 baseColor 贴图、法线贴图与 alpha 裁剪阈值。"""
        from io import BytesIO
        j = self.json

        def load_ref(ref):
            """ref：glTF 的 textureInfo（含 index）。返回 numpy RGBA 或 None。"""
            if not ref:
                return None
            ti = ref.get("index")
            if ti is None:
                return None
            src = j["textures"][ti].get("source")
            if src is None:
                return None
            im = j["images"][src]
            if "bufferView" in im and im.get("bufferView") is not None:
                bv = j["bufferViews"][im["bufferView"]]
                off = bv.get("byteOffset", 0)
                raw = self.blob[off:off + bv["byteLength"]]
                try:
                    return np.asarray(Image.open(BytesIO(raw)).convert("RGBA")).astype(np.float64)
                except Exception:
                    return None
            # 贴图未内嵌：VRF 会把解码后的 PNG 放在 GLB 同级目录，用名字对应
            nm = im.get("name") or ""
            cand = os.path.join(self.dir, os.path.splitext(nm)[0] + ".png")
            if os.path.exists(cand):
                try:
                    return np.asarray(Image.open(cand).convert("RGBA")).astype(np.float64)
                except Exception:
                    return None
            return None

        self.tex = []      # baseColor
        self.ntex = []     # normal map（切线空间）
        self.stex = []     # specular 遮罩（KHR_materials_specular.specularTexture）
        self.cut = []      # alphaCutoff，MASK 材质用它而不是写死阈值
        for mt in j.get("materials", []):
            pbr = mt.get("pbrMetallicRoughness", {})
            self.tex.append(load_ref(pbr.get("baseColorTexture")))
            self.ntex.append(load_ref(mt.get("normalTexture")))
            km = (mt.get("extensions") or {}).get("KHR_materials_specular") or {}
            self.stex.append(load_ref(km.get("specularTexture")))
            self.cut.append(float(mt.get("alphaCutoff", 0.5) or 0.5))

    def vertex_colors(self):
        """每个顶点的底色（双线性采样 baseColor 贴图）。"""
        out = np.tile(np.array([188.0, 192.0, 200.0]), (len(self.POS), 1))
        for mi, img in enumerate(self.tex):
            if img is None:
                continue
            sel = self.MAT == mi
            if not sel.any():
                continue
            h, w = img.shape[:2]
            u = np.clip(self.UV[sel, 0], 0.0, 1.0) * (w - 1)
            v = np.clip(self.UV[sel, 1], 0.0, 1.0) * (h - 1)
            x0 = np.floor(u).astype(int); y0 = np.floor(v).astype(int)
            x1 = np.minimum(x0 + 1, w - 1); y1 = np.minimum(y0 + 1, h - 1)
            fx = (u - x0)[:, None]; fy = (v - y0)[:, None]
            out[sel] = (img[y0, x0] * (1 - fx) * (1 - fy) + img[y1, x0] * (1 - fx) * fy
                        + img[y0, x1] * fx * (1 - fy) + img[y1, x1] * fx * fy)
        return np.clip(out, 0, 255)

    def sample(self, anim_name, t):
        """按动画名与时间采样，返回 node -> {translation/rotation/scale} 覆盖。"""
        a = self.anims.get(anim_name)
        if a is None:
            return {}
        over = {}
        for ch in a["channels"]:
            s = a["samplers"][ch["sampler"]]
            times = _accessor(self.json, self.blob, s["input"]).ravel()
            vals = _accessor(self.json, self.blob, s["output"])
            path = ch["target"]["path"]
            node = ch["target"]["node"]
            if len(times) == 0:
                continue
            interp = s.get("interpolation", "LINEAR")
            if interp == "CUBICSPLINE":
                vals = vals.reshape(len(times), 3, -1)[:, 1, :]
            if t <= times[0]:
                v = vals[0]
            elif t >= times[-1]:
                v = vals[-1]
            else:
                i = int(np.searchsorted(times, t) - 1)
                dt = times[i + 1] - times[i]
                f = 0.0 if dt <= 0 else (t - times[i]) / dt
                v = vals[i] if interp == "STEP" else vals[i] * (1 - f) + vals[i + 1] * f
            over.setdefault(node, {})[path] = v
        return over

    def pose_vertices(self, over):
        """蒙皮后的顶点位置、法线与切线（切线存在 self.last_tan，供法线贴图用）。"""
        world = self._world(over)
        if len(self.skin_joints) and self.ibm is not None and len(self.ibm) == len(self.skin_joints):
            jm = np.stack([world.get(int(n), np.eye(4)) @ self.ibm[k] for k, n in enumerate(self.skin_joints)])
            p = self.POS
            ph = np.concatenate([p, np.ones((len(p), 1))], axis=1)
            acc = np.zeros((len(p), 4))
            nacc = np.zeros((len(p), 4))
            nh = np.concatenate([self.NRM, np.zeros((len(self.NRM), 1))], axis=1)
            th = np.concatenate([self.TAN[:, :3], np.zeros((len(self.TAN), 1))], axis=1)
            tacc = np.zeros((len(p), 4))
            for k in range(4):
                Mk = jm[self.JOINTS[:, k]]
                wk = self.WEIGHTS[:, k:k + 1]
                acc += wk * np.einsum("nij,nj->ni", Mk, ph)
                nacc += wk * np.einsum("nij,nj->ni", Mk, nh)
                tacc += wk * np.einsum("nij,nj->ni", Mk, th)
        else:
            m = np.eye(4)
            for r in self.scene_roots:
                pass
            acc = np.concatenate([self.POS, np.ones((len(self.POS), 1))], axis=1)
            nacc = np.concatenate([self.NRM, np.zeros((len(self.NRM), 1))], axis=1)
            tacc = np.concatenate([self.TAN[:, :3], np.zeros((len(self.TAN), 1))], axis=1)
        self.last_world = world
        pos = acc[:, :3]
        nrm = nacc[:, :3]
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        ln[ln == 0] = 1
        tan = tacc[:, :3]
        lt = np.linalg.norm(tan, axis=1, keepdims=True)
        lt[lt == 0] = 1
        # 手性 w：正交化后备用（B = cross(N,T) * w）
        self.last_tan = np.concatenate([tan / lt, self.TAN[:, 3:4] if len(self.TAN) else np.ones((0, 1))], axis=1)
        return pos, nrm / ln


# ---------------------------------------------------------------- 相机 / 光栅化

def look_at(eye, target, up):
    f = target - eye
    f = f / (np.linalg.norm(f) or 1)
    r = np.cross(f, up)
    r = r / (np.linalg.norm(r) or 1)
    u = np.cross(r, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = r, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m


def perspective(fovy, aspect, near, far):
    t = 1.0 / math.tan(fovy / 2)
    m = np.zeros((4, 4))
    m[0, 0] = t / aspect
    m[1, 1] = t
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


def _sample_tex(img, u, v):
    """双线性采样（逐像素调用，u/v 已是像素坐标）。"""
    h, w = img.shape[:2]
    x0 = np.clip(np.floor(u).astype(np.int32), 0, w - 1)
    y0 = np.clip(np.floor(v).astype(np.int32), 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    y1 = np.clip(y0 + 1, 0, h - 1)
    fx = (u - x0)[..., None]
    fy = (v - y0)[..., None]
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y1, x0] * (1 - fx) * fy
            + img[y0, x1] * fx * (1 - fy) + img[y1, x1] * fx * fy)


def rasterize(tri, nrm_tri, uv_tri, tan_tri, mat_tri, tex, ntex, cuts, idx, W, H, lights=None,
              amb=0.54, alpha_test=0.35, stex=None, view=None, spec_pow=26.0, spec_amt=0.6,
              rim_amt=0.26):
    """逐像素插值法线/切线/UV，采样 baseColor + 法线贴图 + 高光遮罩并做光照。

    高光与边缘光是社区公认自研渲染器最缺的两项（dota2-model-viewer 的 Known issues
    明确写了 specular / rim 缺失），补上之后"塑料平"的观感会明显改善。
    """
    img = np.zeros((H, W, 3), np.float64)
    zbuf = np.full((H, W), np.inf)
    mask = np.zeros((H, W), bool)
    order = np.argsort(tri[:, :, 2].mean(axis=1))[::-1]      # 远到近
    for i in order:
        x, y, z = tri[i, :, 0], tri[i, :, 1], tri[i, :, 2]
        x0 = max(int(np.floor(x.min())), 0)
        x1 = min(int(np.ceil(x.max())) + 1, W)
        y0 = max(int(np.floor(y.min())), 0)
        y1 = min(int(np.ceil(y.max())) + 1, H)
        if x1 <= x0 or y1 <= y0:
            continue
        PX, PY = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(d) < 1e-12:
            continue
        l0 = ((y[1] - y[2]) * (PX - x[2]) + (x[2] - x[1]) * (PY - y[2])) / d
        l1 = ((y[2] - y[0]) * (PX - x[2]) + (x[0] - x[2]) * (PY - y[2])) / d
        l2 = 1.0 - l0 - l1
        better = (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
        if not better.any():
            continue
        zz = l0 * z[0] + l1 * z[1] + l2 * z[2]
        sub = zbuf[y0:y1, x0:x1]
        better &= (zz < sub)
        if not better.any():
            continue

        n = (l0[..., None] * nrm_tri[i, 0] + l1[..., None] * nrm_tri[i, 1]
             + l2[..., None] * nrm_tri[i, 2])
        nn = np.linalg.norm(n, axis=2, keepdims=True)
        nn[nn == 0] = 1.0
        nd = n / nn

        # 双面渲染时把法线按视线翻正（等价于 gl_FrontFacing ? N : -N）：
        # 否则被看到的内表面会按"背对光源"着色，补洞的地方会变成黑块。
        if view is not None:
            nd = np.where(((nd @ view) < 0.0)[..., None], -nd, nd)

        uv = (l0[..., None] * uv_tri[i, 0] + l1[..., None] * uv_tri[i, 1]
              + l2[..., None] * uv_tri[i, 2])

        # 法线贴图：用插值后的切线建 TBN，把切线空间法线转到世界空间再打光。
        # 之前完全没用这张图（GLB 里其实一直带着），所以画面是"平的"。
        nmap = ntex[mat_tri[i]] if (ntex and 0 <= mat_tri[i] < len(ntex)) else None
        if nmap is not None:
            tn = (l0[..., None] * tan_tri[i, 0] + l1[..., None] * tan_tri[i, 1]
                  + l2[..., None] * tan_tri[i, 2])
            tl = np.linalg.norm(tn, axis=2, keepdims=True)
            tl[tl == 0] = 1.0
            T = tn / tl
            B = np.cross(nd, T)
            h, w = nmap.shape[:2]
            ns = _sample_tex(nmap, (uv[..., 0] % 1.0) * (w - 1), (uv[..., 1] % 1.0) * (h - 1))
            nt = ns[..., :3] / 127.5 - 1.0
            nd = T * nt[..., 0:1] + B * nt[..., 1:2] + nd * nt[..., 2:3]
            nn = np.linalg.norm(nd, axis=2, keepdims=True)
            nn[nn == 0] = 1.0
            nd = nd / nn

        lam = np.zeros(nd.shape[:2])
        for lv, lw in (lights or []):
            lam += np.clip(nd @ lv, 0.0, 1.0) * lw
        shade = amb + 1.15 * lam

        # 高光：Blinn-Phong 半角向量，强度由材质自带的 specularTexture 当遮罩
        if view is not None and lights:
            lv, lw = lights[0]
            hv = lv + view
            hn = float(np.linalg.norm(hv))
            if hn > 1e-6:
                hv = hv / hn
                sp = np.clip(nd @ hv, 0.0, 1.0) ** spec_pow
                specmask = 1.0
                st = stex[mat_tri[i]] if (stex and 0 <= mat_tri[i] < len(stex)) else None
                if st is not None:
                    sh, sw = st.shape[:2]
                    ss = _sample_tex(st, (uv[..., 0] % 1.0) * (sw - 1),
                                     (uv[..., 1] % 1.0) * (sh - 1))
                    specmask = ss[..., 0] / 255.0
                shade = shade + sp * specmask * spec_amt * lw

        # 边缘光：正视处为 0、掠射处最强，用来勾勒轮廓
        if view is not None:
            rim = (1.0 - np.clip(np.abs(nd @ view), 0.0, 1.0)) ** 3.0
            shade = shade + rim * rim_amt

        shade = np.clip(shade, 0.0, 1.9)

        t = tex[mat_tri[i]] if 0 <= mat_tri[i] < len(tex) else None
        if t is not None:
            th, tw = t.shape[:2]
            # Source 2 的 UV 可超出 [0,1]，表示纹理环绕平铺（REPEAT），必须取模而非夹取
            alb = _sample_tex(t, (uv[..., 0] % 1.0) * (tw - 1),
                              (uv[..., 1] % 1.0) * (th - 1))
            if t.shape[2] == 4:
                # alpha 裁剪阈值取材质自带的 alphaCutoff（MASK 材质），拿不到才退回默认值
                thr = cuts[mat_tri[i]] if (cuts and 0 <= mat_tri[i] < len(cuts)) else alpha_test
                if thr <= 1.0:
                    thr *= 255.0          # glTF 的 alphaCutoff 是 0..1，贴图 alpha 是 0..255
                better = better & (alb[..., 3] >= thr)
                if not better.any():
                    continue
            col = alb[..., :3] * shade[..., None]
        else:
            col = np.array([188.0, 192.0, 200.0]) * shade[..., None]
        sub[better] = zz[better]
        img[y0:y1, x0:x1][better] = np.clip(col[better], 0, 255)
        mask[y0:y1, x0:x1][better] = True
    return img, mask


def _bind_anchor(model, framing="full"):
    """取景锚点 (center, radius)，**只按绑定姿势算**，整个动作期间复用同一个值。

    这是"人物以自身为中心晃动"的关键。原先 render() 每帧用当前帧的包围盒重算
    center/radius，等于相机每帧重新瞄准：idle 的注视点就会飘 dz=0.36，而相机在
    yaw=25° 的斜侧位，目标沿深度移动会被透视投影换算成横向位移——于是人物看起来
    "绕着空间里某个点甩"，而不是自身在晃动（实测 idle 抖 9x14 px、attack 抖 77x19 px）。

    锚点缓存挂在模型上，并以顶点数为键——merge_parts() 会改变顶点数，据此自动失效。
    """
    cache = getattr(model, "_anchor_cache", None)
    if cache and cache[0] == len(model.POS) and cache[1] == framing:
        return cache[2][0], cache[2][1]

    lo, hi = model.POS.min(axis=0), model.POS.max(axis=0)
    center = (lo + hi) / 2.0
    radius = max(float(np.linalg.norm(hi - lo)) / 2.0, 1e-3)

    if framing == "portrait":
        # 对准头部骨骼做胸像取景：官方头像就是这种构图（同样取绑定姿势）
        body_h = float(hi[1] - lo[1])
        head_y = float(lo[1] + 0.86 * body_h)
        world = model._world({})
        for ni, nd in enumerate(model.nodes):
            nm = (nd.get("name") or "").lower()
            if "head" in nm and "hitloc" not in nm and "look" not in nm and ni in world:
                head_y = float(world[ni][1, 3])
                break
        center = np.array([center[0], head_y, center[2]])
        radius = max(body_h * 0.30, 1e-3)

    model._anchor_cache = (len(model.POS), framing, (center, radius))
    return center, radius


def _root_xz_delta(model, over):
    """root 骨骼相对绑定姿势的 XZ 位移（root motion），Y 不动。

    跑动/突进这类动作，root 会沿 Z 走出 7.6 / 2.2 个单位；桌宠要的是**原地**做动作，
    所以把这段平移从顶点上扣掉。身体上下起伏在 Y 上，保留，那正是自然的晃动。
    """
    if not len(model.skin_joints):
        return None
    names = [model.nodes[int(n)].get("name") or "" for n in model.skin_joints]
    if "root" not in names:
        return None
    node = int(model.skin_joints[names.index("root")])
    bind = model._world({})
    cur = getattr(model, "last_world", None) or {}
    if node not in bind or node not in cur:
        return None
    d = cur[node][:3, 3] - bind[node][:3, 3]
    d[1] = 0.0
    return d


def render(model, over, size=420, yaw=25.0, pitch=8.0, fov=32.0, mode="shaded",
           light=(0.42, 0.78, 0.46), ss=2, textured=True, framing="full", plant=True):
    """渲染一帧。

    plant=True：扣掉 root 骨骼在 XZ 上的位移（root motion），让人物**原地**做动作。
    不做这一步，run/attack 的 root 会沿 Z 走出 7.6 / 2.2 个单位，人物直接跑出画面。
    Y 方向保留，所以身体的起伏（这才是自然的"人物自身晃动"）不会被抹掉。
    """
    pos, nrm = model.pose_vertices(over)
    if len(pos) == 0:
        return Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 取景锚点一律取绑定姿势并全程复用，见 _bind_anchor 的说明
    center, radius = _bind_anchor(model, framing)

    if plant:
        d = _root_xz_delta(model, over)
        if d is not None:
            pos = pos - d

    ya, pa = math.radians(yaw), math.radians(pitch)
    dist = radius / math.tan(math.radians(fov) / 2) * 1.05
    eye = center + np.array([math.sin(ya) * math.cos(pa), math.sin(pa),
                             math.cos(ya) * math.cos(pa)]) * dist
    view = look_at(eye, center, np.array([0.0, 1.0, 0.0]))
    proj = perspective(math.radians(fov), 1.0, max(dist - radius * 2, 0.01), dist + radius * 4)

    W = H = int(size * ss)
    vw = (view @ np.concatenate([pos, np.ones((len(pos), 1))], axis=1).T).T
    clip = (proj @ vw.T).T
    w = clip[:, 3:4].copy()
    w[np.abs(w) < 1e-9] = 1e-9
    ndc = clip[:, :3] / w
    scr = np.stack([(ndc[:, 0] * 0.5 + 0.5) * W,
                    (1.0 - (ndc[:, 1] * 0.5 + 0.5)) * H,
                    -vw[:, 2]], axis=1)

    to_eye = eye - center
    to_eye = to_eye / (np.linalg.norm(to_eye) or 1.0)
    L1 = to_eye + np.array([0.34, 0.46, 0.0])
    L1 = L1 / (np.linalg.norm(L1) or 1.0)          # 跟随相机的主光（头灯偏上偏左）
    L2 = -to_eye + np.array([0.0, 0.35, 0.15])
    L2 = L2 / (np.linalg.norm(L2) or 1.0)          # 背侧补光
    lights = [(L1, 0.92), (L2, 0.26)]

    L = np.array(light, np.float64)
    L = L / np.linalg.norm(L)

    # 法线自定向：若整体朝内（正面几乎收不到光），翻转一次
    fwd = np.clip(nrm @ L, 0.0, 1.0).mean()
    bwd = np.clip(-(nrm @ L), 0.0, 1.0).mean()
    flipped = bwd > fwd
    if flipped:
        nrm = -nrm

    tri = scr[model.TRIS]
    nrm_tri = nrm[model.TRIS]
    uv_tri = model.UV[model.TRIS]
    tan = getattr(model, "last_tan", None)
    if tan is None or len(tan) != len(model.POS):
        tan = np.tile(np.array([1.0, 0.0, 0.0, 1.0]), (len(model.POS), 1))
    if flipped:
        tan = tan.copy()
        tan[:, :3] = -tan[:, :3]
    tan_tri = tan[model.TRIS][:, :, :3]
    mat_tri = model.MAT[model.TRIS[:, 0]]
    area = ((tri[:, 1, 0] - tri[:, 0, 0]) * (tri[:, 2, 1] - tri[:, 0, 1])
            - (tri[:, 2, 0] - tri[:, 0, 0]) * (tri[:, 1, 1] - tri[:, 0, 1]))
    # 不做单面剔除：饰品部件与本体混在一起时，部件的朝向判定常有局部不一致，
    # 早先的 `keep = area > 0` 会把兜帽正面的一小块当背面剔掉，画面就在脑门处"透空"、
    # 直接看到后面的身体（实测洞像素占角色 4.3%，双面渲染后回到本体基线）。
    # 双面渲染后由 z-buffer 决定遮挡，并在着色时把法线按视线翻正（见 rasterize），
    # 所以补上的背面不会变成黑块。
    keep = np.ones(len(area), bool)
    tex = model.tex if textured else [None] * len(model.tex)
    ntex = model.ntex if textured else [None] * len(getattr(model, "ntex", []))
    if os.environ.get('D2_SHOW_NORMALS'):
        print('  法线朝向: 原始lam均值=%.3f 翻转后=%.3f -> %s' % (fwd, bwd, '已翻转' if flipped else '保持'))
    rgb, mask = rasterize(tri[keep], nrm_tri[keep], uv_tri[keep], tan_tri[keep], mat_tri[keep],
                          tex, ntex, model.cut, model.TRIS[keep], W, H, lights,
                          stex=(model.stex if textured else None), view=to_eye)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB").resize((size, size), Image.LANCZOS)
    al = Image.fromarray((mask * 255).astype(np.uint8), "L").resize((size, size), Image.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(al)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", required=True)
    ap.add_argument("--anim", default=None)
    ap.add_argument("--t", type=float, default=0.0)
    ap.add_argument("--yaw", type=float, default=25.0)
    ap.add_argument("--pitch", type=float, default=8.0)
    ap.add_argument("--size", type=int, default=420)
    ap.add_argument("--mode", default="shaded")
    ap.add_argument("--gray", action="store_true")
    ap.add_argument("--out", default="out.png")
    ap.add_argument("--bake", default=None)
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--yaws", type=int, default=1)
    ap.add_argument("--outdir", default="frames3d")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    m = Model(args.glb)
    print("顶点 %d  三角形 %d  骨骼 %d  动画 %d" % (
        len(m.POS), len(m.TRIS), len(m.skin_joints), len(m.anims)))
    if args.list:
        names = sorted(m.anims)
        print("动画名（前 40）:", names[:40])
        return

    if args.bake:
        a = m.anims.get(args.bake)
        if a is None:
            print("没有动画:", args.bake)
            return
        s = a["samplers"][0]
        times = _accessor(m.json, m.blob, s["input"]).ravel()
        span = float(times[-1] - times[0]) if len(times) else 1.0
        os.makedirs(args.outdir, exist_ok=True)
        n = 0
        for yi in range(max(1, args.yaws)):
            yaw = -180.0 + 360.0 * yi / max(1, args.yaws)
            for fi in range(args.frames):
                t = times[0] + span * fi / max(1, args.frames)
                img = render(m, m.sample(args.bake, t), size=args.size, yaw=yaw,
                             pitch=args.pitch, mode=args.mode, textured=not args.gray)
                img.save(os.path.join(args.outdir, "y%02d_f%03d.png" % (yi, fi)))
                n += 1
        print("烘焙完成 %d 帧 -> %s" % (n, args.outdir))
        return

    img = render(m, m.sample(args.anim, args.t) if args.anim else {}, size=args.size,
                 yaw=args.yaw, pitch=args.pitch, mode=args.mode, textured=not args.gray)
    img.save(args.out)
    a = np.asarray(img)
    cov = (a[:, :, 3] > 8).mean()
    print("已输出 %s  透明占比=%.1f%%" % (args.out, (1 - cov) * 100))


if __name__ == "__main__":
    main()
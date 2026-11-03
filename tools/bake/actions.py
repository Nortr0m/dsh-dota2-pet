#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dota 2 英雄"动作库"：从模型文件里读取游戏里真正能做的动作，并能渲染出来。

数据来源（全部来自本机游戏文件，不联网）：
    models/heroes/<hero>/<hero>.vmdl_c 的 ASEQ 块
        -> 132 条序列(m_localS1SeqDescArray)：m_sName / m_bLooping / m_activityArray
        -> m_activityArray 里是 Source 2 的官方活动名：ACT_DOTA_IDLE / ACT_DOTA_RUN /
           ACT_DOTA_ATTACK / ACT_DOTA_CAST_ABILITY_x / ACT_DOTA_DIE / ACT_DOTA_TAUNT ...
    GLB 动画（由 VRF 导出）
        -> 采样器 input 的时间范围 = 每个动作的真实时长

用法：
    python actions.py --list
    python actions.py --json actions_slark.json
    python actions.py --sheet --out sheet.png            # 关键动作 4 帧×8 动作 对比图
    python actions.py --gif --action @run --out run.gif  # 把某个动作烘成动图
"""

import argparse
import json
import os
import subprocess
import sys

import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import itemlib  # noqa: E402
import render   # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

# 活动名 -> 中文类别（用于给桌宠/界面按"能做什么"分组）
CATS = [
    ("待机", ("ACT_DOTA_IDLE", "idle")),
    ("移动", ("ACT_DOTA_RUN", "ACT_DOTA_WALK", "run", "walk", "turns")),
    ("攻击", ("ACT_DOTA_ATTACK", "attack")),
    ("施法", ("ACT_DOTA_CAST", "channel", "cast")),
    ("技能", ("pounce", "dark_pact", "shadow_dance", "essence", "ult")),
    ("受击", ("ACT_DOTA_STUN", "flail", "injured", "stun", "hurt")),
    ("死亡", ("ACT_DOTA_DIE", "death", "die")),
    ("胜利", ("ACT_DOTA_VICTORY", "victory", "win")),
    ("出生/传送", ("spawn", "teleport", "ACT_DOTA_SPAWN")),
    ("展示/嘲讽", ("loadout", "portrait", "taunt", "laugh", "ACT_DOTA_TAUNT")),
]

# 分类的 ASCII 版：PowerShell 桌面窗口脚本必须保持纯 ASCII（5.1 按 ANSI 读无 BOM 脚本），
# 所以 manifest 里中英两份都写，前端用中文、桌宠独立窗口用 ASCII。
CAT_EN = {
    "待机": "idle", "移动": "move", "攻击": "attack", "施法": "cast",
    "技能": "skill", "受击": "hit", "死亡": "death", "胜利": "victory",
    "出生/传送": "spawn", "展示/嘲讽": "show", "其他": "other",
}


def categorize(name, acts):
    hay = (' '.join(acts) + ' ' + (name or '')).lower()
    for label, keys in CATS:
        for k in keys:
            if k.lower() in hay:
                return label
    return "其他"


class ActionLib(object):
    def __init__(self, glb, aseq=None, agrp=None, parts=None, attach=None):
        self.m = render.Model(glb)
        self.parts = list(parts or [])
        if self.parts:
            # 本体只有身体，兜帽/披风/护腕/肩甲/武器是独立模型，必须并上才像游戏里。
            # attach 指明哪些部件要挂到挂点骨骼上（武器在源文件里没有蒙皮数据与正确位置）。
            self.m.merge_parts(self.parts, attach=attach)
        self.durations = self._durations()
        self.seqs = []
        if aseq and os.path.exists(aseq):
            kv = itemlib.parse_block_dump(aseq, "ASEQ") or {}
            for s in (kv.get("m_localS1SeqDescArray") or []):
                name = s.get("m_sName")
                flags = s.get("m_flags") or {}
                acts = [a.get("m_name") for a in (s.get("m_activityArray") or [])
                        if isinstance(a, dict)]
                self.seqs.append({
                    "name": name,
                    "activities": acts,
                    "loop": str(flags.get("m_bLooping")) == "true",
                    "hidden": str(flags.get("m_bHidden")) == "true",
                    "autoplay": str(flags.get("m_bAutoplay")) == "true",
                    "weightlist": s.get("m_nLocalWeightlist"),
                    "category": categorize(name, acts),
                    "in_glb": name in self.m.anims,
                    "duration": self.durations.get(name),
                })
        # GLB 里存在但 ASEQ 没描述的（合成分支等）
        known = set(x["name"] for x in self.seqs)
        for g in sorted(self.m.anims):
            if g not in known:
                self.seqs.append({
                    "name": g, "activities": [], "loop": False, "hidden": False,
                    "autoplay": False, "weightlist": None,
                    "category": categorize(g, []), "in_glb": True,
                    "duration": self.durations.get(g),
                })

    def _durations(self):
        out = {}
        for name, a in self.m.anims.items():
            lo, hi = None, None
            for s in a.get("samplers", []):
                t = render._accessor(self.m.json, self.m.blob, s["input"]).ravel()
                if len(t):
                    lo = t[0] if lo is None else min(lo, t[0])
                    hi = t[-1] if hi is None else max(hi, t[-1])
            out[name] = round(float(hi - lo), 3) if hi is not None else None
        return out

    def playable(self, only_glb=True, include_hidden=False):
        rows = [r for r in self.seqs if r["in_glb"] or not only_glb]
        if not include_hidden:
            rows = [r for r in rows if not r["hidden"]]
        return rows

    def by_category(self):
        out = {}
        for r in self.playable():
            out.setdefault(r["category"], []).append(r)
        return out


# ------------------------------------------------------------------ 渲染

def frames_of(m, anim, n, t0=0.0, span=None, exclusive=False):
    """取 n 帧姿态。exclusive=True 时按 i/n 取点（循环动作的首尾不会重复）。"""
    a = m.anims.get(anim)
    if a is None:
        return []
    dur = 0.0
    for s in a.get("samplers", []):
        t = render._accessor(m.json, m.blob, s["input"]).ravel()
        if len(t):
            dur = max(dur, float(t[-1] - t[0]))
    span = span or max(dur, 1e-3)
    if exclusive:
        return [m.sample(anim, t0 + span * i / max(1, n)) for i in range(n)]
    return [m.sample(anim, t0 + span * i / max(1, n - 1)) for i in range(n)]


# 内置基础动作集：动作名 -> 帧数（帧率由时长反推，保持游戏原速）
BAKE_SET = [
    ("idle_anim", 24),
    ("run", 16),
    ("attack_anim", 12),
    ("pounce_anim", 14),
    ("dark_pact_anim", 14),
    ("shadow_dance", 12),
    ("death", 16),
    ("victory", 20),
]


def pick_bake_set(lib, fps=16):
    """按英雄自己的动作库挑一套通用动作（各英雄动作名不同，不能写死）。

    挑选顺序：先按常见名字精确命中，命不中再在该类别里取**时长最短**的
    （短动作更适合循环与反复播放；也让英雄之间风格一致）。
    排除 versus_attack / overkilled 这类 40 秒级的特殊动作，以及 generic_channel。
    """
    rows = [r for r in lib.playable() if r["in_glb"]]

    def by_name(names):
        for n in names:
            for r in rows:
                if r["name"] == n:
                    return r
        return None

    def by_cat(cats, shortest=True, exclude=()):
        c = [r for r in rows if r["category"] in cats
             and not any(x in r["name"] for x in exclude)]
        if not c:
            return None
        c.sort(key=lambda r: (r["duration"] or 0), reverse=not shortest)
        return c[0]

    picks = []
    # 待机 / 移动 / 攻击
    picks.append(by_name(['idle_anim', 'idle', 'idle2_anim']) or by_cat(('待机',)))
    picks.append(by_name(['run_anim', 'run', '@run_anim', '@run']) or by_cat(('移动',)))
    picks.append(by_name(['attack_anim', 'attack1_anim', 'attack2_anim', 'attack'])
                 or by_cat(('攻击',), exclude=('versus',)))

    # 专属技能：先技能类，不够再从施法类补（很多英雄技能被归在施法里）
    # 归一化去重：hero 常有 shadow_dance / @@shadow_dance_latch / xxx_injured 这类同源变体
    def norm(n):
        n = n.lstrip('@_')
        if n.endswith('_anim'):
            n = n[:-5]
        for junk in ('injured', 'latch', 'loadout', 'effigy'):
            n = n.replace(junk, '').replace('__', '_').strip('_')
        return n

    abil = [r for r in rows if r["category"] == "技能"]
    abil += [r for r in rows if r["category"] == "施法"
             and 'channel' not in r["name"] and not r["loop"]]
    abil.sort(key=lambda r: (r["duration"] or 0))
    picked_keys, abil2 = set(), []
    for r in abil:
        k = norm(r["name"])
        if not k or k in picked_keys:
            continue
        picked_keys.add(k)
        abil2.append(r)
    picks += abil2[:2]

    # 死亡 / 胜利
    picks.append(by_name(['death', 'dieBack', 'dieForward', 'die'])
                 or by_cat(('死亡',), exclude=('overkilled',)))
    picks.append(by_name(['victory', 'victory_loop_anim', 'victory_loop'])
                 or by_cat(('胜利',)))

    out, seen = [], set()
    for r in picks:
        if not r or r["name"] in seen:
            continue
        seen.add(r["name"])
        n = int(round((r["duration"] or 1.0) * fps))
        out.append((r["name"], max(12, min(32, n))))
    return out


def hole_stats(img):
    """返回 (洞像素数, 洞占不透明像素的百分比)。

    "洞"= 被不透明像素包围的透明像素：说明那里本该有面却没有任何三角形覆盖
    （典型成因是把正面三角形当背面剔掉了）。注意：武器与身体之间围出的背景
    也会被算进来，属于正常形状，所以判据用的是"占比是否异常大"而非零容忍。
    """
    holes, op, _ = _holes_mask(img)
    n = int(holes.sum())
    total = int(op.sum())
    return n, (n / total * 100.0 if total else 0.0)


def _holes_mask(img):
    """返回 (洞掩码, 不透明掩码, 不透明像素的 y 范围)。"""
    import collections
    a = np.asarray(img.convert("RGBA"))[:, :, 3] > 8
    h, w = a.shape
    trans = ~a
    reach = np.zeros_like(trans)
    dq = collections.deque()
    for x in range(w):
        for y in (0, h - 1):
            if trans[y, x] and not reach[y, x]:
                reach[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if trans[y, x] and not reach[y, x]:
                reach[y, x] = True
                dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and trans[ny, nx] and not reach[ny, nx]:
                reach[ny, nx] = True
                dq.append((ny, nx))
    ys, _xs = np.where(a)
    return trans & ~reach, a, (int(ys.min()), int(ys.max())) if len(ys) else (0, h - 1)


def _max_blob(mask):
    """掩码里最大连通块的大小。"""
    import collections
    if not mask.any():
        return 0
    seen = np.zeros_like(mask)
    h, w = mask.shape
    big = 0
    ys, xs = np.where(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        dq = collections.deque([(y0, x0)])
        seen[y0, x0] = True
        size = 0
        while dq:
            y, x = dq.popleft()
            size += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    dq.append((ny, nx))
        big = max(big, size)
    return big


def frame_quality(img):
    """单帧质量指标：整体透空、头部区域（上 40%）透空、头部最大单块。

    头部区域单独看是因为"脑门透空"正是用户可见的那个缺陷，而整体透空里混着
    武器与身体之间围出的合法背景，单看整体无法区分。
    """
    holes, op, (y0, y1) = _holes_mask(img)
    total = max(1, int(op.sum()))
    head_cut = int(y0 + (y1 - y0) * 0.4)
    head_holes = holes[:head_cut]
    head_op = max(1, int(op[:head_cut].sum()))
    return {
        "holeFracMax": round(float(holes.sum()) / total * 100.0, 3),
        "holePixelsMax": int(holes.sum()),
        "headHoleFracMax": round(float(head_holes.sum()) / head_op * 100.0, 3),
        "headHoleBlobMax": int(_max_blob(head_holes)),
    }


def bake(m, outdir, items=None, size=220, yaw=25.0, pitch=6.0, yaws=1, loops=None,
         cats=None, log=print):
    """把动作烘成帧序列 + manifest.json（桌宠包内置素材，离线渲染一次即可）。

    manifest 里除了帧与帧率，还写入分类（中文给界面 / ASCII 给必须保持纯 ASCII 的
    PowerShell 桌面窗口脚本），这样前端右键菜单可以按英雄实际拥有的动作动态生成。
    """
    import json as _json
    import datetime as _dt
    items = items or BAKE_SET
    loops = loops or {}
    cats = cats or {}
    os.makedirs(outdir, exist_ok=True)
    # build 戳：帧文件名不变，前端要靠它做缓存失效，否则浏览器缓存旧图（路由给了 max-age）
    manifest = {"build": _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S"),
                "size": size, "pitch": pitch, "yaws": yaws, "actions": {}}
    for anim, n in items:
        if anim not in m.anims:
            log("跳过（模型里没有）: %s" % anim)
            continue
        cat = cats.get(anim) or '其他'
        entry = {"frames": n, "loop": bool(loops.get(anim)),
                 "category": cat, "categoryEn": CAT_EN.get(cat, 'other'), "yaws": []}
        for yi in range(max(1, yaws)):
            # 朝向从正面（0°）开始往后排：曾经的 -180 起始会把"背面"排在 yaws[0]，
            # 而消费方（前端 / 桌面窗口）默认取第一组，结果桌宠一直用背面示人。
            yaw_i = yaw if yaws == 1 else (360.0 * yi / yaws)
            poses = frames_of(m, anim, n, exclusive=True)
            names = []
            q = {"holeFracMax": 0.0, "holePixelsMax": 0, "headHoleFracMax": 0.0, "headHoleBlobMax": 0}
            for i, pose in enumerate(poses):
                img = render.render(m, pose, size=size, yaw=yaw_i, pitch=pitch,
                                    mode="shaded", textured=True)
                # 记录"透空"指标：单面剔除曾把兜帽正面的面剔掉、脑门透空（占角色 4.3%），
                # 这个指标用来防回归（测试里会断言头部区域不能有大洞）
                fq = frame_quality(img)
                for k, v in fq.items():
                    q[k] = max(q[k], v)
                fn = "%s_y%02d_f%03d.png" % (anim.replace("@", ""), yi, i)
                img.save(os.path.join(outdir, fn))
                names.append(fn)
            entry["yaws"].append({"yaw": round(yaw_i, 1), "files": names})
            entry.update(q)
        dur = lib_dur(m, anim)
        entry["duration"] = dur
        entry["fps"] = round(n / dur, 2) if dur else 12
        manifest["actions"][anim] = entry
        log("烘焙 %-18s %2d 帧  时长 %.2fs  %d 帧/秒" % (anim, n, dur or 0, entry["fps"]))
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        _json.dump(manifest, fh, ensure_ascii=False, indent=1)
    return manifest


def lib_dur(m, anim):
    a = m.anims.get(anim)
    if a is None:
        return 0.0
    dur = 0.0
    for s in a.get("samplers", []):
        t = render._accessor(m.json, m.blob, s["input"]).ravel()
        if len(t):
            dur = max(dur, float(t[-1] - t[0]))
    return round(dur, 3)


def sheet(m, actions, cols, size, yaw, pitch, out):
    rows = len(actions)
    tiles = []
    for row, (anim, label) in enumerate(actions):
        poses = frames_of(m, anim, cols)
        if not poses:
            continue
        for pose in poses:
            tiles.append(render.render(m, pose, size=size, yaw=yaw, pitch=pitch,
                                        mode="shaded", textured=True))
    if not tiles:
        raise SystemExit("没有任何可渲染的动作")
    pad, cap = 6, 26
    w = cols * size + pad * (cols + 1)
    h = rows * (size + cap) + pad
    canvas = Image.new("RGBA", (w, h), (16, 11, 6, 255))
    d = ImageDraw.Draw(canvas)
    k = 0
    for row, (anim, label) in enumerate(actions):
        y = pad + row * (size + cap)
        d.text((pad, y + 4), label, fill=(240, 205, 130, 255))
        poses = frames_of(m, anim, cols)
        for _ in poses:
            canvas.alpha_composite(tiles[k], (pad + (k % cols) * (size + pad), y + cap))
            k += 1
    canvas.save(out)
    return out


def gif(m, anim, n, size, yaw, pitch, fps, out):
    poses = frames_of(m, anim, n)
    if not poses:
        raise SystemExit("没有这个动作: %s" % anim)
    imgs = [render.render(m, p, size=size, yaw=yaw, pitch=pitch, mode="shaded",
                          textured=True).convert("RGBA") for p in poses]
    bg = Image.new("RGBA", imgs[0].size, (16, 11, 6, 255))
    flat = []
    for im in imgs:
        c = bg.copy()
        c.alpha_composite(im)
        flat.append(c.convert("P", palette=Image.ADAPTIVE, colors=255))
    flat[0].save(out, save_all=True, append_images=flat[1:], duration=int(1000 / fps),
                 loop=0, disposal=2)
    return out


KEY_ACTIONS = ["idle_anim", "@run", "attack_anim", "pounce_anim",
               "dark_pact_anim", "@shadow_dance", "death", "victory"]

# 游戏里 ACT_DOTA_CAST_ABILITY_n / 专属活动名 -> 英雄技能（从英雄定义文件读）
ABILITY_ACT = {
    "ACT_DOTA_CAST_ABILITY_1": "Ability1", "ACT_DOTA_CAST_ABILITY_2": "Ability2",
    "ACT_DOTA_CAST_ABILITY_3": "Ability3", "ACT_DOTA_CAST_ABILITY_4": "Ability4",
    "ACT_DOTA_CAST_ABILITY_5": "Ability5", "ACT_DOTA_CAST_ABILITY_6": "Ability6",
}


def _hero_node(hero_file):
    """在 npc_dota_hero_x.txt 里找到英雄本体节点（外层是 DOTAHeroes）。"""
    if not hero_file or not os.path.exists(hero_file):
        return None
    with open(hero_file, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    root, _ = itemlib.parse_kv(text)
    stack = [root]
    while stack:
        d = stack.pop()
        if not isinstance(d, dict):
            continue
        if 'Ability1' in d or 'ItemSlots' in d:
            return d
        for v in d.values():
            if isinstance(v, dict):
                stack.append(v)
    return None


def read_abilities(hero_file):
    """Ability1..6 -> 技能内部名（就是 ACT_DOTA_CAST_ABILITY_n 对应的技能）。"""
    node = _hero_node(hero_file) or {}
    out = {}
    for i in range(1, 7):
        v = node.get('Ability%d' % i)
        if isinstance(v, str) and v:
            out['Ability%d' % i] = v
    return out


def read_item_slots(hero_file):
    """ItemSlots -> {槽位名: {slot_index, text}}，即游戏允许该英雄挂饰品的槽位。"""
    node = _hero_node(hero_file) or {}
    slots = node.get('ItemSlots')
    out = {}
    if isinstance(slots, dict):
        for idx, s in sorted(slots.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 999):
            if not isinstance(s, dict):
                continue
            nm = s.get('SlotName')
            if isinstance(nm, str):
                out[nm] = {'index': int(idx) if idx.isdigit() else idx,
                           'text': s.get('SlotText') or ''}
    return out


def weapon_attach_bone(weapon_glb, base_glb, vpk=None, gameinfo=None, model_path=None):
    """用**装备模型自带的骨架**推出它该挂到本体哪根骨骼上（这是游戏的统一规则）。

    依据（直接从客户端文件读出来的）：
      斯拉克武器  DATA.m_modelSkeleton.m_boneName = ['root', 'weapon_2']
      食人魔武器                                   = ['weapon3_0']
      本体模型同样有这些骨骼（斯拉克 48 根里有 weapon_2，食人魔里也有 weapon3_0）。
    即：**装备模型里与英雄骨架同名的骨骼就是挂点**，引擎按名字对接。
    所以既不需要按英雄写死映射，也不需要靠画面位置去猜。

    注意：不能从导出的 GLB 里读这些骨骼名——VRF 导出装备时把骨架丢了（skins 为空），
    必须回到 VPK 读该模型的 DATA 块。
    """
    try:
        bm = render.Model(base_glb)
    except Exception:
        return None
    body_set = {((bm.nodes[int(x)].get("name") or "").lower()): (bm.nodes[int(x)].get("name") or "")
                for x in bm.skin_joints}
    if not body_set:
        return None

    wnames = []
    try:
        wm = render.Model(weapon_glb)
        if len(wm.skin_joints):
            wnames = [wm.nodes[int(x)].get("name") or "" for x in wm.skin_joints]
    except Exception:
        wnames = []

    if not wnames and vpk and gameinfo:
        wnames = _vpk_bone_names(weapon_glb, vpk, gameinfo, model_path=model_path) or []

    hits = [n for n in wnames if n.lower() in body_set and n.lower() != "root"]
    if not hits:
        return None
    hits.sort(key=lambda s: (0 if "weapon" in s.lower() else 1, len(s)))
    return body_set[hits[0].lower()]


def _vpk_bone_names(glb, vpk, gameinfo, model_path=None):
    """从 VPK 读某个装备模型的 DATA 块，抠出它骨架的骨骼名列表。

    model_path 优先——调用方本来就知道武器的真实 VPK 路径（r['model']），
    从 GLB 文件名反推路径是不可靠的（缓存名把目录拼平了）。
    """
    exe = os.path.join(HERE, "tools", "Source2Viewer-CLI.exe")
    if not os.path.exists(exe):
        return []
    import sys as _sys
    if os.environ.get("D2_DEBUG_ATTACH"):
        print("[attach] exe=%s vpk=%s model_path=%s" % (exe, vpk, model_path), file=_sys.stderr)
    cands = []
    if model_path:
        cands.append(model_path.replace("\\", "/") + ("" if model_path.endswith("_c") else "_c"))
    stem = os.path.splitext(os.path.basename(glb))[0]
    parts = stem.split("_")
    for i in range(1, len(parts)):
        cands.append("models/items/" + "/".join(parts[:i]) + "/" + "/".join(parts[i:]) + ".vmdl_c")
        cands.append("models/heroes/" + "/".join(parts[:i]) + "/" + "/".join(parts[i:]) + ".vmdl_c")
    for cand in cands:
        cmd = [exe, "-i", os.path.join(vpk, "pak01_dir.vpk"),
               "--vpk_filepath", cand, "-b", "DATA", "--game", gameinfo]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=90)
        except Exception as exc:
            # 不再静默吞异常：这里曾经因为漏了 `import subprocess`、被 `except: continue`
            # 吞掉 NameError，表现成"读了 VPK 但永远拿不到骨骼名"，排查了很久。
            print("[attach] 读取 %s 失败: %s" % (cand, exc), file=sys.stderr)
            continue
        ok = "--- Data for block" in (r.stdout or "")
        if os.environ.get("D2_DEBUG_ATTACH"):
            print("[attach] cand=%s ok=%s len=%d" % (cand, ok, len(r.stdout or "")), file=_sys.stderr)
        if ok:
            names = _parse_bone_names(r.stdout)
            if os.environ.get("D2_DEBUG_ATTACH"):
                print("[attach] parsed=%s" % (names,), file=_sys.stderr)
            if names:
                return names
    return []


def _parse_bone_names(text):
    """从 VRF 的 DATA dump 文本里抠出 m_boneName 列表。

    VRF 的 KV3 dump 长这样（键**不带引号**）：
        m_boneName =
        [
            "root",
            "weapon_2",
        ]
    """
    import re
    m = re.search(r'\bm_boneName\s*=\s*\[', text)
    if not m:
        return []
    j = text.find("[", m.end() - 1)
    k = text.find("]", j) if j >= 0 else -1
    if j < 0 or k < 0:
        return []
    out = []
    for tok in text[j + 1:k].split(","):
        tok = tok.strip().strip('"').strip()
        if tok:
            out.append(tok)
    return out


def default_parts(hero, items_path, model_dir=None, base_glb=None, vpk=None, gameinfo=None):
    """导出该英雄的默认部件（兜帽/披风/护腕/肩甲/武器）GLB。

    model_dir：本体模型所在目录名。英雄 slug 不一定等于模型目录
    （司夜刺客 nyx_assassin 的模型在 models/heroes/nerubian_assassin/），
    所以从本体 GLB 的文件名推最稳。

    返回 (部件列表, 挂点表)。挂点表用于把武器这类"没有蒙皮数据、顶点在局部空间"的部件
    绑到挂点骨骼上；不绑它会停在角色脚底。
    """
    import wardrobe
    w = wardrobe.Wardrobe(items_path)
    out, attach = [], {}

    for r in w.loadout(hero, defaults=True, model_dir=model_dir):
        glb, ok = wardrobe.export_model(r['model'])
        if not ok:
            continue
        out.append(glb)
        if 'weapon' in (r.get('slot') or '').lower():
            bone = weapon_attach_bone(glb, base_glb, vpk=vpk, gameinfo=gameinfo,
                                      model_path=r.get('model')) if base_glb else None
            if bone:
                attach[glb] = bone
                print("  武器挂点: %s <- %s" % (bone, os.path.basename(glb)))
            else:
                print("  ⚠ %s 的武器没能在本体骨架里找到同名骨骼，武器可能无法定位" % hero)
    return out, attach


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hero", default="slark")
    ap.add_argument("--glb", default=os.path.join(HERE, "export", "slark.glb"))
    ap.add_argument("--aseq", default=os.path.join(HERE, "data", "slark_ASEQ.txt"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--sheet", action="store_true")
    ap.add_argument("--gif", action="store_true")
    ap.add_argument("--action", default="@run")
    ap.add_argument("--actions", default="")
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--fps", type=int, default=12)
    ap.add_argument("--size", type=int, default=240)
    ap.add_argument("--yaw", type=float, default=30.0)
    ap.add_argument("--pitch", type=float, default=8.0)
    ap.add_argument("--out", default=os.path.join(HERE, "actions_out.png"))
    ap.add_argument("--parts", default="default", choices=["none", "default"],
                    help="default=把默认饰品（兜帽/披风/护腕/肩甲/武器）并上，none=只渲染身体")
    ap.add_argument("--items", default=os.path.join(HERE, "data", "items_game.txt"))
    ap.add_argument("--hero-file", default=os.path.join(HERE, "data", "npc_dota_hero_slark.txt"))
    ap.add_argument("--abilities", action="store_true")
    ap.add_argument("--bake-dir", default=None, help="烘焙内置动作帧到这个目录")
    ap.add_argument("--bake-set", default='auto', choices=['auto', 'slark'],
                    help="auto=按该英雄动作库自动挑一套；slark=沿用斯拉克那套动作名")
    ap.add_argument("--bake-yaws", type=int, default=2)
    ap.add_argument("--bake-size", type=int, default=220)
    a = ap.parse_args()

    parts, attach = ([], {})
    if a.parts == "default":
        parts, attach = default_parts(a.hero, a.items,
                                      model_dir=os.path.basename(a.glb).replace('.glb', ''))
        print("并上默认部件 %d 件（挂点绑定 %d 件）" % (len(parts), len(attach)))
    lib = ActionLib(a.glb, a.aseq, parts=parts, attach=attach)

    if a.abilities:
        ab = read_abilities(a.hero_file)
        slots = read_item_slots(a.hero_file)
        print("技能 -> 施法/专属动作：")
        for slot in sorted(ab, key=lambda s: int(s[-1])):
            key = ab[slot]
            acts = [k for k, v in ABILITY_ACT.items() if v == slot]
            # 英雄专属活动名兜底：slark_pounce -> ACT_DOTA_SLARK_POUNCE / pounce_anim
            short = key.split('_', 1)[1] if '_' in key else key
            toks = [t for t in short.split('_') if len(t) > 3]
            hits = []
            for r in lib.playable():
                hay = (' '.join(r['activities']) + ' ' + r['name']).lower()
                if any(k.lower() in hay for k in acts) or (toks and all(t in hay for t in toks)):
                    hits.append(r)
            hits.sort(key=lambda r: (r['duration'] or 0))
            print("  %-9s %-26s %s" % (slot, key,
                  ', '.join('%s(%.2fs)' % (h['name'], h['duration'] or 0) for h in hits[:3]) or '-'))
        if slots:
            print("可挂饰品槽位（ItemSlots）：%d 个 -> %s" % (
                len(slots), ', '.join('%s#%s' % (k, v['index']) for k, v in slots.items())))

    if a.list or not (a.sheet or a.gif or a.json):
        groups = lib.by_category()
        total = sum(len(v) for v in groups.values())
        print("英雄 %s：可播放动作 %d 个（GLB 动画 %d 条 / ASEQ 描述 %d 条）"
              % (a.hero, total, len(lib.m.anims), len(lib.seqs)))
        for cat in [c[0] for c in CATS] + ["其他"]:
            rows = groups.get(cat)
            if not rows:
                continue
            print("  【%s】%d 个" % (cat, len(rows)))
            for r in rows[:8]:
                print("     %-26s %-34s %5s s  %s" % (
                    r["name"], ','.join(r["activities"])[:34] or '-',
                    ('%.2f' % r["duration"]) if r["duration"] else '?',
                    '循环' if r["loop"] else ''))
            if len(rows) > 8:
                print("     … 其余 %d 个" % (len(rows) - 8))

    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"hero": a.hero, "glb": os.path.basename(a.glb),
                       "actions": lib.seqs}, fh, ensure_ascii=False, indent=1)
        print("动作库已写入 %s" % a.json)

    if a.bake_dir:
        loops = {r["name"]: r["loop"] for r in lib.seqs}
        items = BAKE_SET if a.bake_set == 'slark' else pick_bake_set(lib)
        print("烘焙动作集：%s" % (', '.join('%s×%d' % (n, c) for n, c in items) or '（空）'))
        bake(lib.m, a.bake_dir, items=items, size=a.bake_size, yaws=a.bake_yaws, loops=loops)
        print("内置动作帧 -> %s" % a.bake_dir)

    if a.sheet:
        want = [x.strip() for x in a.actions.split(',') if x.strip()] or KEY_ACTIONS
        pairs = [(x, x) for x in want if x in lib.m.anims]
        missing = [x for x in want if x not in lib.m.anims]
        if missing:
            print("跳过（GLB 里没有）:", missing)
        out = sheet(lib.m, pairs, a.cols, a.size, a.yaw, a.pitch, a.out)
        print("动作对比图 -> %s" % out)

    if a.gif:
        out = gif(lib.m, a.action, a.frames, a.size, a.yaw, a.pitch, a.fps, a.out)
        print("动图 -> %s (%d 帧 @ %dfps)" % (out, a.frames, a.fps))


if __name__ == "__main__":
    main()
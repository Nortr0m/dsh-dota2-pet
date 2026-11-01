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
import sys

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
    def __init__(self, glb, aseq=None, agrp=None, parts=None):
        self.m = render.Model(glb)
        self.parts = list(parts or [])
        if self.parts:
            # 本体只有身体，兜帽/披风/护腕/肩甲/武器是独立模型，必须并上才像游戏里
            self.m.merge_parts(self.parts)
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
    ("@run", 16),
    ("attack_anim", 12),
    ("pounce_anim", 14),
    ("dark_pact_anim", 14),
    ("@shadow_dance", 12),
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
    picks.append(by_name(['@run', 'run_anim', 'run', '@run_anim']) or by_cat(('移动',)))
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


def bake(m, outdir, items=None, size=220, yaw=25.0, pitch=6.0, yaws=1, loops=None,
         cats=None, log=print):
    """把动作烘成帧序列 + manifest.json（桌宠包内置素材，离线渲染一次即可）。

    manifest 里除了帧与帧率，还写入分类（中文给界面 / ASCII 给必须保持纯 ASCII 的
    PowerShell 桌面窗口脚本），这样前端右键菜单可以按英雄实际拥有的动作动态生成。
    """
    import json as _json
    items = items or BAKE_SET
    loops = loops or {}
    cats = cats or {}
    os.makedirs(outdir, exist_ok=True)
    manifest = {"size": size, "pitch": pitch, "yaws": yaws, "actions": {}}
    for anim, n in items:
        if anim not in m.anims:
            log("跳过（模型里没有）: %s" % anim)
            continue
        cat = cats.get(anim) or '其他'
        entry = {"frames": n, "loop": bool(loops.get(anim)),
                 "category": cat, "categoryEn": CAT_EN.get(cat, 'other'), "yaws": []}
        for yi in range(max(1, yaws)):
            yaw_i = yaw if yaws == 1 else (-180.0 + 360.0 * yi / yaws)
            poses = frames_of(m, anim, n, exclusive=True)
            names = []
            for i, pose in enumerate(poses):
                img = render.render(m, pose, size=size, yaw=yaw_i, pitch=pitch,
                                    mode="shaded", textured=True)
                fn = "%s_y%02d_f%03d.png" % (anim.replace("@", ""), yi, i)
                img.save(os.path.join(outdir, fn))
                names.append(fn)
            entry["yaws"].append({"yaw": round(yaw_i, 1), "files": names})
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


def default_parts(hero, items_path, model_dir=None):
    """导出该英雄的默认部件（兜帽/披风/护腕/肩甲/武器）GLB。

    model_dir：本体模型所在目录名。英雄 slug 不一定等于模型目录
    （司夜刺客 nyx_assassin 的模型在 models/heroes/nerubian_assassin/），
    所以从本体 GLB 的文件名推最稳。
    """
    import wardrobe
    w = wardrobe.Wardrobe(items_path)
    out = []
    for r in w.loadout(hero, defaults=True, model_dir=model_dir):
        glb, ok = wardrobe.export_model(r['model'])
        if ok:
            out.append(glb)
    return out


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

    parts = default_parts(a.hero, a.items,
                          model_dir=os.path.basename(a.glb).replace('.glb', '')) if a.parts == "default" else []
    if a.parts == "default":
        print("并上默认部件 %d 件" % len(parts))
    lib = ActionLib(a.glb, a.aseq, parts=parts)

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
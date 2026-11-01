#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dota 2 英雄"衣橱"：从真实游戏条目（items_game.txt）驱动 3D 组装与渲染。

链路：
    items_game.txt 条目 / 套装(item_sets)
        -> 取该英雄的 model_player_per_hero 路径
        -> Source2Viewer-CLI 从 pak01_dir.vpk 导出 GLB（带材质）
        -> render.Model.merge_parts() 按最近骨骼刚性绑定并进本体
        -> 动画采样 + 蒙皮 + 光栅化 -> PNG

用法：
    python wardrobe.py --hero slark --list-sets
    python wardrobe.py --hero slark --set ocean_conquerer --out dress.png
    python wardrobe.py --hero slark --compare base default ocean_conquerer --out cmp.png
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import itemlib  # noqa: E402
import render  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

DEFAULT_VPK = r"D:\SteamLibrary\steamapps\common\dota 2 beta\game\dota"
DEFAULT_GAMEINFO = os.path.join(DEFAULT_VPK, "gameinfo.gi")
EXE = os.path.join(HERE, "tools", "Source2Viewer-CLI.exe")
ITEMS = os.path.join(HERE, "data", "items_game.txt")
CACHE = os.path.join(HERE, "wardrobe")


# ------------------------------------------------------------------ 数据层

class Wardrobe(object):
    def __init__(self, items_path=ITEMS):
        self.root, _ = itemlib.load_items(items_path)
        self.lib = itemlib.ItemLib(self.root)
        self.sets = self.lib.root.get("item_sets") or {}
        self._name2key = None

    @property
    def name2key(self):
        if self._name2key is None:
            m = {}
            for k, v in self.lib.items.items():
                if isinstance(v, dict) and isinstance(v.get("name"), str):
                    m.setdefault(v["name"], k)
            self._name2key = m
        return self._name2key

    def hero_of(self, item_key):
        return self.lib.used_by(item_key)

    def sets_for(self, hero):
        hk = self.lib.hero_key(hero)
        out = []
        for sid, s in self.sets.items():
            if not isinstance(s, dict):
                continue
            items = s.get("items")
            if not isinstance(items, dict):
                continue
            rows, heroes = [], set()
            for nm in items:
                k = self.name2key.get(nm)
                if not k:
                    continue
                heroes.update(self.hero_of(k))
                model = self.lib.model_for_hero(k, hero)
                rows.append({"key": k, "name": nm, "slot": self.lib.slot_of(k),
                             "model": model})
            if hk in heroes and rows:
                out.append({"id": sid, "name": s.get("name") or sid, "items": rows})
        out.sort(key=lambda x: x["id"])
        return out

    def loadout(self, hero, set_id=None, slots=None, defaults=False):
        """返回 [{slot,name,model,source}]，每槽位一件。"""
        rows = []
        if defaults or set_id == "default":
            for it in self.lib.hero_items(hero):
                if it["model"] and it["model"].startswith("models/heroes/%s/" % hero):
                    rows.append({"slot": it["slot"], "name": it["name"],
                                 "model": it["model"], "source": "默认"})
        if set_id and set_id not in ("default",):
            cand = [s for s in self.sets_for(hero) if s["id"] == set_id]
            if not cand:
                raise SystemExit("找不到套装: %s" % set_id)
            for it in cand[0]["items"]:
                rows.append({"slot": it["slot"], "name": it["name"],
                             "model": it["model"], "source": cand[0]["id"]})
        if slots:
            # slots: {slot: item_name_or_key}
            for slot, want in slots.items():
                hit = None
                for it in self.lib.hero_items(hero):
                    if (it["slot"] or "").lower() != slot.lower():
                        continue
                    if it["key"] == want or it["name"] == want or \
                       (it["model"] or "").find(want) >= 0:
                        hit = it
                        break
                if not hit:
                    raise SystemExit("槽位 %s 找不到条目: %s" % (slot, want))
                rows = [r for r in rows if (r["slot"] or "").lower() != slot.lower()]
                rows.append({"slot": hit["slot"], "name": hit["name"],
                             "model": hit["model"], "source": "手动"})
        # 去重：同槽位保留最后一个
        by_slot = {}
        for r in rows:
            by_slot[(r["slot"] or "?").lower()] = r
        return [by_slot[k] for k in sorted(by_slot)]


# ------------------------------------------------------------------ 导出层

def export_model(model_path, vpk=DEFAULT_VPK, gameinfo=DEFAULT_GAMEINFO, exe=EXE, cache=CACHE):
    """把 .vmdl（VPK 内为 _c）导出成 GLB，带本地缓存。"""
    slug = model_path.replace("\\", "/").rstrip("/").split("/")[-1].replace(".vmdl", "")
    sub = model_path.replace("\\", "/").split("/")
    tag = "_".join(sub[-3:-1] + [slug]) if len(sub) >= 3 else slug
    out = os.path.join(cache, tag + ".glb")
    if os.path.exists(out) and os.path.getsize(out) > 1024:
        return out, True
    os.makedirs(cache, exist_ok=True)
    vpk_path = model_path.replace("\\", "/")
    if not vpk_path.endswith("_c"):
        vpk_path += "_c"
    cmd = [exe, "-i", os.path.join(vpk, "pak01_dir.vpk"), "--vpk_filepath", vpk_path,
           "-o", out, "-d", "--gltf_export_format", "glb", "--gltf_export_materials",
           "--game", gameinfo, "--threads", "4"]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return out, os.path.exists(out) and os.path.getsize(out) > 1024


# ------------------------------------------------------------------ 渲染层

def build(hero, base_glb, parts, pose_anim="portrait", pose_t=0.4, yaw=25.0, pitch=8.0,
          size=560, textured=True):
    m = render.Model(base_glb)
    n0 = len(m.POS)
    glbs = [p for p in parts if p]
    if glbs:
        m.merge_parts(glbs)
    stat = {"verts": len(m.POS), "tris": len(m.TRIS), "parts": len(glbs),
            "added": len(m.POS) - n0, "tex": len(m.tex)}
    img = render.render(m, m.sample(pose_anim, pose_t) if pose_anim else {},
                        size=size, yaw=yaw, pitch=pitch, mode="shaded", textured=textured)
    return img, stat


def label_strip(tiles, labels, pad=8, bg=(18, 12, 7, 255), fg=(240, 205, 130, 255)):
    w = sum(t.width for t in tiles) + pad * (len(tiles) + 1)
    h = max(t.height for t in tiles) + 54
    canvas = Image.new("RGBA", (w, h), bg)
    x = pad
    d = ImageDraw.Draw(canvas)
    for t, lab in zip(tiles, labels):
        canvas.alpha_composite(t, (x, 40))
        tw = d.textlength(lab)
        d.text((x + (t.width - tw) / 2, 14), lab, fill=fg)
        x += t.width + pad
    return canvas


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hero", default="slark")
    ap.add_argument("--items", default=ITEMS)
    ap.add_argument("--base", default=os.path.join(HERE, "export", "slark.glb"))
    ap.add_argument("--vpk", default=DEFAULT_VPK)
    ap.add_argument("--set")
    ap.add_argument("--compare", nargs="*", default=None,
                    help="对比多个 loadout：base / default / <set_id>")
    ap.add_argument("--anim", default="portrait")
    ap.add_argument("--t", type=float, default=0.4)
    ap.add_argument("--yaw", type=float, default=25.0)
    ap.add_argument("--pitch", type=float, default=8.0)
    ap.add_argument("--size", type=int, default=560)
    ap.add_argument("--list-sets", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "wardrobe_out.png"))
    a = ap.parse_args()

    w = Wardrobe(a.items)

    if a.list_sets:
        sets = w.sets_for(a.hero)
        print("英雄 %s 的套装：%d 套" % (a.hero, len(sets)))
        for s in sets:
            print("  %-42s %s" % (s["id"], s["name"]))
            for it in s["items"]:
                print("      [%-8s] %-34s %s" % (it["slot"], it["name"], it["model"]))
        return

    if a.compare is not None:
        names = a.compare or ["base", "default"]
        tiles, labels, stats = [], [], []
        for nm in names:
            parts, lab = [], nm
            if nm == "base":
                lab = "本体（无饰品）"
            elif nm == "default":
                rows = w.loadout(a.hero, defaults=True)
                lab = "默认套装"
                parts = [export_model(r["model"], cache=os.path.join(HERE, "wardrobe"))[0]
                         for r in rows]
            else:
                rows = w.loadout(a.hero, set_id=nm)
                s = [x for x in w.sets_for(a.hero) if x["id"] == nm]
                lab = (s[0]["name"] if s else nm).replace("#DOTA_Set_", "")
                parts = [export_model(r["model"], cache=os.path.join(HERE, "wardrobe"))[0]
                         for r in rows]
            img, st = build(a.hero, a.base, parts, a.anim, a.t, a.yaw, a.pitch, a.size)
            tiles.append(img)
            labels.append("%s  %d tris" % (lab, st["tris"]))
            stats.append((nm, st))
        out = label_strip(tiles, labels)
        out.save(a.out)
        for nm, st in stats:
            print("%-24s 顶点 %5d  三角 %5d  部件 %d  (+%d verts)  贴图 %d" % (
                nm, st["verts"], st["tris"], st["parts"], st["added"], st["tex"]))
        print("已输出 %s" % a.out)
        return

    rows = w.loadout(a.hero, set_id=a.set or "default")
    parts = []
    for r in rows:
        glb, ok = export_model(r["model"], cache=os.path.join(HERE, "wardrobe"))
        print("[%-8s] %-32s %s" % (r["slot"], r["name"], "OK" if ok else "导出失败"))
        if ok:
            parts.append(glb)
    img, st = build(a.hero, a.base, parts, a.anim, a.t, a.yaw, a.pitch, a.size)
    img.save(a.out)
    print("顶点 %d 三角 %d 部件 %d 贴图 %d -> %s" % (
        st["verts"], st["tris"], st["parts"], st["tex"], a.out))


if __name__ == "__main__":
    main()
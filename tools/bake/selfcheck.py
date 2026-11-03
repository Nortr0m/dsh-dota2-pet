#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""渲染自检（诊断用，需要本机游戏文件，不进 CI）。

背景：`render()` 曾用 `keep = area > 0` 做单面剔除；饰品部件与本体混在一起时朝向
判定局部不一致，兜帽正面的一小块会被当背面剔掉 —— 表现是脑门"透空"，全身透空像素
从 3% 级跳到 7%。本脚本把相关指标打出来，并对**明显异常**（头部出现大块空洞、
整体透空超过阈值）返回非零退出码。

用法（在插件仓库的 tools/bake 目录下）：
    python selfcheck.py --glb <models>/slark.glb --part <models>/heroes_slark_hood.glb
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np          # noqa: E402
import render               # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb", default=os.path.join(HERE, "export", "slark.glb"))
    ap.add_argument("--part", default=os.path.join(HERE, "wardrobe", "heroes_slark_hood.glb"))
    ap.add_argument("--anim", default="idle_anim")
    ap.add_argument("--size", type=int, default=220)
    ap.add_argument("--head-blob-max", type=int, default=200,
                    help="头部区域允许的最大空洞（像素）；越大说明有面被剔掉")
    ap.add_argument("--hole-frac-max", type=float, default=20.0,
                    help="整体透空占角色的百分比上限")
    a = ap.parse_args()

    for p in (a.glb, a.part):
        if not os.path.exists(p):
            print("跳过：找不到 %s（本检查需要本机游戏文件）" % p)
            return 0

    from actions import frame_quality

    print("渲染自检：%s + %s" % (os.path.basename(a.glb), os.path.basename(a.part)))
    fails = []
    for yaw, label in ((0, "正面"), (90, "侧面"), (180, "背面")):
        body = render.Model(a.glb)
        both = render.Model(a.glb)
        both.merge_parts([a.part])
        ib = render.render(body, body.sample(a.anim, 0.4), size=a.size, yaw=yaw, pitch=6,
                           mode="shaded", textured=True)
        io = render.render(both, both.sample(a.anim, 0.4), size=a.size, yaw=yaw, pitch=6,
                           mode="shaded", textured=True)
        d = int((np.abs(np.asarray(ib.convert("RGBA")).astype(np.int16)
                        - np.asarray(io.convert("RGBA")).astype(np.int16)).sum(axis=2) > 12).sum())
        # 参考值（修正后实测）：正面约 860、侧面约 640、背面约 130。
        # 这里只拦"几乎为零"——那说明整个兜帽都被剔掉了。
        print("  %s yaw=%3d：加兜帽后差异 %5d 像素 %s" % (label, yaw, d,
              "（几乎不可见，可疑）" if d < 60 else ""))
        if d < 60:
            fails.append(("兜帽不可见", label, d))

    m = render.Model(a.glb)
    m.merge_parts([a.part])
    img = render.render(m, m.sample(a.anim, 0.4), size=a.size, yaw=0, pitch=6,
                        mode="shaded", textured=True)
    q = frame_quality(img)
    print("  透空：整体 %.2f%%（上限 %.1f%%）  头部 %.2f%%  头部最大空洞 %d px（上限 %d）"
          % (q["holeFracMax"], a.hole_frac_max, q["headHoleFracMax"],
             q["headHoleBlobMax"], a.head_blob_max))
    if q["holeFracMax"] > a.hole_frac_max:
        fails.append(("整体透空", q["holeFracMax"]))
    if q["headHoleBlobMax"] > a.head_blob_max:
        fails.append(("头部空洞", q["headHoleBlobMax"]))

    if fails:
        print("\n自检未通过：%s" % fails)
        return 1
    print("\n自检通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
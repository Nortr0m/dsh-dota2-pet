#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dota 2 items_game.txt 解析器（Valve KeyValues / KV3-text，无第三方依赖）

用途：从游戏文件（VPK 抽出的 scripts/items/items_game.txt）里读取真实的
饰品条目：槽位(item_slot)、模型(model_player / model_player_per_hero)、
所属英雄(used_by_heroes)，并按 prefab 链做继承，供 3D 渲染器按"槽位"组合。

用法：
    python itemlib.py --list-hero slark
    python itemlib.py --hero slark --slot weapon --sample 5
    python itemlib.py --hero slark --item "Slark_Item_..." --json
"""

import argparse
import json
import os
import re
import sys


# ------------------------------------------------------------------ KV 解析

_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[{}]|[^\s{}"]+')


def _unquote(tok):
    if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
        body = tok[1:-1]
        return body.replace('\\"', '"').replace('\\\\', '\\')
    return tok


def _tokenize(text):
    """逐字符扫描：正确处理引号内的 //、转义、以及裸露的 { }。

    之前用"先正则去注释再分词"的做法，遇到值里含 http:// 或 // 的条目时
    会把行尾引号一起吃掉，导致整个 token 流失步、items 块被截断（实测只解析出
    654 条，而文件里实际有 2.6 万条 prefab）。
    """
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            j = text.find('\n', i)
            i = n if j < 0 else j + 1
            continue
        if c == '"':
            j = i + 1
            buf = []
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            yield ''.join(buf)
            i = j + 1
            continue
        if c == '{' or c == '}':
            yield c
            i += 1
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n{}"':
            j += 1
        yield text[i:j]
        i = j


def parse_kv(text, _depth=0):
    """把 KV 文本解析成 (dict, bases)；重复键转为 list 保留全部取值。"""
    toks = list(_tokenize(text))
    pos = [0]
    bases = []

    def parse_block(depth):
        out = {}
        while pos[0] < len(toks):
            t = toks[pos[0]]
            if t == '}':
                pos[0] += 1
                return out
            if t == '{':
                pos[0] += 1
                continue
            key = t
            pos[0] += 1
            if pos[0] >= len(toks):
                out[key] = ''
                break
            nxt = toks[pos[0]]
            if nxt == '{':
                pos[0] += 1
                out[key] = parse_block(depth + 1)
            else:
                pos[0] += 1
                val = nxt
                if key == '#base':
                    bases.append(val)
                if key in out:
                    if not isinstance(out[key], list):
                        out[key] = [out[key]]
                    out[key].append(val)
                else:
                    out[key] = val
        return out

    return parse_block(_depth), bases


def load_items(path):
    with open(path, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    root, bases = parse_kv(text)
    return root, bases


# ------------------------------------------------------------ KV3-text 解析

def parse_kv3(text):
    """解析 KV3-text（ASEQ/AGRP/DATA 这类块）。

    与经典 KV（items_game.txt）的区别：`key = value` 用等号、数组用 [ ] 加逗号、
    值可带类型前缀（resource:"..."）、注释是 <!-- -->。
    """
    toks = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in ' \t\r\n':
            i += 1
            continue
        if text.startswith('<!--', i):
            j = text.find('-->', i)
            i = n if j < 0 else j + 3
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n:
                if text[j] == '\\' and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    break
                buf.append(text[j])
                j += 1
            toks.append(('str', ''.join(buf)))
            i = j + 1
            continue
        if c in '{}[]=,':
            toks.append((c, c))
            i += 1
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n{}[]=,"':
            j += 1
        toks.append(('str', text[i:j]))
        i = j

    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else (None, None)

    def take():
        t = peek()
        pos[0] += 1
        return t

    def skip_comma():
        while pos[0] < len(toks) and toks[pos[0]][0] == ',':
            pos[0] += 1

    def parse_value():
        kind, val = peek()
        if kind == '{':
            take()
            return parse_object()
        if kind == '[':
            take()
            arr = []
            while True:
                skip_comma()
                k2, _v2 = peek()
                if k2 is None or k2 == ']':
                    take()
                    return arr
                arr.append(parse_value())
        if kind == 'str':
            take()
            if val.endswith(':') and peek()[0] == 'str':   # resource:"path"
                return take()[1]
            return val
        take()
        return val

    def parse_object():
        out = {}
        while True:
            skip_comma()
            k, kv = peek()
            if k is None or k == '}':
                take()
                return out
            take()
            if peek()[0] == '=':
                take()
                skip_comma()
                out[kv] = parse_value()
            else:
                out[kv] = ''
        return out

    return parse_value()


def parse_block_dump(path, block):
    """从 VRF `-b <BLOCK>` 的文本输出里取出该块并解析成 KV3 结构。"""
    with open(path, encoding='utf-8', errors='replace') as fh:
        text = fh.read()
    marker = '--- Data for block "%s" ---' % block
    i = text.find(marker)
    if i < 0:
        return None
    return parse_kv3(text[i + len(marker):])


# ---------------------------------------------------------------- prefab 继承

def _as_dict(v):
    return v if isinstance(v, dict) else {}


class ItemLib(object):
    def __init__(self, root):
        # 外层通常包一层 "items_game" { ... }
        if isinstance(root.get('items_game'), dict) and 'items' in root['items_game']:
            root = root['items_game']
        self.root = root
        self.items = _as_dict(root.get('items'))
        self.prefabs = _as_dict(root.get('prefabs'))
        self._cache = {}

    # -- prefab 链：自身 -> prefab -> prefab 的 prefab ...
    def resolved(self, key):
        if key in self._cache:
            return self._cache[key]
        chain = []
        k = key
        seen = set()
        while k and k not in seen:
            seen.add(k)
            d = _as_dict(self.items.get(k)) or None
            if d is None:
                break
            chain.append((k, d))
            pf = d.get('prefab')
            if isinstance(pf, list):
                pf = pf[0]
            k = pf if isinstance(pf, str) else None
        out = {}
        for _k, d in reversed(chain):          # 先基类后子类，子类覆盖
            for kk, vv in d.items():
                if kk == 'prefab':
                    continue
                if isinstance(vv, dict) and isinstance(out.get(kk), dict):
                    merged = dict(out[kk])
                    merged.update(vv)
                    out[kk] = merged
                else:
                    out[kk] = vv
        # 合并 prefabs 定义（item_slot 等常在 prefabs 里）
        for _k, d in reversed(chain):
            pf = d.get('prefab')
            if isinstance(pf, list):
                pf = pf[0]
            if isinstance(pf, str) and pf in self.prefabs:
                for kk, vv in _as_dict(self.prefabs[pf]).items():
                    if kk not in out:
                        out[kk] = vv
        self._cache[key] = out
        return out

    # -- 英雄名归一：slark / npc_dota_hero_slark 都接受
    @staticmethod
    def hero_key(hero):
        h = hero.lower()
        if h.startswith('npc_dota_hero_'):
            return h
        if h.startswith('npc_'):
            return 'npc_dota_hero_' + h[4:]
        return 'npc_dota_hero_' + h

    def model_for_hero(self, key, hero):
        """取条目在本英雄上的模型路径。"""
        d = self.resolved(key)
        hk = self.hero_key(hero)
        per = d.get('model_player_per_hero')
        if isinstance(per, dict):
            for k, v in per.items():
                if k.lower() == hk and isinstance(v, str) and v:
                    return v
        mp = d.get('model_player')
        if isinstance(mp, str) and mp:
            return mp
        return None

    def used_by(self, key):
        d = self.resolved(key)
        ub = d.get('used_by_heroes')
        if not isinstance(ub, dict):
            return []
        return [k for k, v in ub.items() if str(v) not in ('0', '', 'false')]

    def slot_of(self, key):
        d = self.resolved(key)
        s = d.get('item_slot')
        if isinstance(s, list):
            s = s[0]
        if isinstance(s, str) and s:
            return s
        # 有些条目用 prefab 名表达槽位
        pf = d.get('prefab')
        if isinstance(pf, str):
            for cand in ('head', 'shoulder', 'arms', 'weapon', 'back', 'tail',
                         'legs', 'misc', 'armor', 'neck', 'belt', 'mount'):
                if cand in pf:
                    return cand
        return None

    def hero_items(self, hero, only_with_model=True):
        """返回该英雄的饰品清单：[{key,name,slot,model,prefab}]"""
        hk = self.hero_key(hero)
        out = []
        for key, raw in self.items.items():
            d = _as_dict(raw)
            if not d:
                continue
            ub = d.get('used_by_heroes')
            per = d.get('model_player_per_hero')
            hit = False
            if isinstance(ub, dict) and any(k.lower() == hk for k in ub):
                hit = True
            if not hit and isinstance(per, dict) and any(k.lower() == hk for k in per):
                hit = True
            if not hit:
                continue
            model = self.model_for_hero(key, hero)
            if only_with_model and not model:
                continue
            nm = d.get('name')
            if isinstance(nm, list):
                nm = nm[0]
            pf = d.get('prefab')
            if isinstance(pf, list):
                pf = pf[0]
            out.append({'key': key, 'name': nm or key, 'slot': self.slot_of(key),
                        'model': model, 'prefab': pf})
        return out

    def all_heroes_with_items(self):
        heroes = set()
        for key, raw in self.items.items():
            d = _as_dict(raw)
            ub = d.get('used_by_heroes')
            if isinstance(ub, dict):
                for k, v in ub.items():
                    if str(v) not in ('0', '', 'false'):
                        heroes.add(k)
            per = d.get('model_player_per_hero')
            if isinstance(per, dict):
                for k in per:
                    heroes.add(k)
        return sorted(heroes)


# ---------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--items', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    'data', 'items_game.txt'))
    ap.add_argument('--hero')
    ap.add_argument('--slot')
    ap.add_argument('--sample', type=int, default=8)
    ap.add_argument('--item')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--heroes', action='store_true')
    ap.add_argument('--stats', action='store_true')
    a = ap.parse_args()

    root, bases = load_items(a.items)
    lib = ItemLib(root)

    if bases:
        print('#bases: %d -> %s' % (len(bases), bases[:5]), file=sys.stderr)

    if a.stats:
        print('items: %d' % len(lib.items))
        print('prefabs: %d' % len(lib.prefabs))
        print('root keys: %s' % sorted(root.keys())[:20])
        heroes = lib.all_heroes_with_items()
        print('heroes with items: %d' % len(heroes))
        return

    if a.heroes:
        for h in lib.all_heroes_with_items():
            print(h)
        return

    if a.item:
        print(json.dumps(lib.resolved(a.item), ensure_ascii=False, indent=1)[:4000])
        return

    if a.hero:
        rows = lib.hero_items(a.hero)
        if a.slot:
            rows = [r for r in rows if (r['slot'] or '').lower() == a.slot.lower()]
        by_slot = {}
        for r in rows:
            by_slot.setdefault(r['slot'] or '(none)', []).append(r)
        print('英雄 %s：%d 件带模型的饰品' % (a.hero, len(rows)))
        for s in sorted(by_slot):
            print('  [%s] %d 件' % (s, len(by_slot[s])))
            for r in by_slot[s][:a.sample]:
                print('     %-46s %s' % (r['name'][:46], r['model']))
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
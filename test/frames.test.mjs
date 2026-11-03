/**
 * 内置动作帧的完整性校验：遍历 assets/frames 下的每个英雄。
 *
 *   node --test
 *
 * 加英雄时不需要改这里——目录一多，校验自动跟着覆盖。
 */

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.join(import.meta.dirname, '..', 'assets', 'frames');

function heroDirs() {
  if (!fs.existsSync(ROOT)) return [];
  return fs.readdirSync(ROOT)
    .filter((n) => fs.statSync(path.join(ROOT, n)).isDirectory())
    .sort();
}

const heroes = heroDirs();

test('至少内置了一个英雄的动作帧', () => {
  assert.ok(heroes.length >= 1, 'assets/frames 下没有任何英雄目录');
});

for (const hero of heroes) {
  test(`[${hero}] manifest 结构完整`, () => {
    const dir = path.join(ROOT, hero);
    const man = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
    assert.ok(man.size >= 64, 'size 不合理');
    const names = Object.keys(man.actions || {});
    assert.ok(names.length >= 4, `${hero} 动作太少: ${names.length}`);

    // 至少要有能循环播放的待机动作——桌宠默认就播它
    const looping = names.filter((n) => man.actions[n].loop);
    assert.ok(looping.length >= 1, `${hero} 没有任何循环动作`);

    for (const n of names) {
      const a = man.actions[n];
      assert.ok(a.frames >= 2, `${n} 帧数太少`);
      assert.ok(a.fps > 0 && a.fps <= 60, `${n} 帧率不合理: ${a.fps}`);
      assert.ok(Array.isArray(a.yaws) && a.yaws.length >= 1, `${n} 缺 yaws`);
      // 透空指标（烘焙时写入）：单面剔除曾把兜帽正面剔掉，脑门出现空洞。
      // 阈值放得较松——整体透空里混着"武器与身体之间围出的合法的背景"，只拦明显异常。
      if (typeof a.holeFracMax === 'number') {
        assert.ok(a.holeFracMax < 20, `${hero}/${n} 整体透空 ${a.holeFracMax}%（>20% 说明有面被剔掉）`);
      }
      if (typeof a.headHoleBlobMax === 'number') {
        assert.ok(a.headHoleBlobMax <= 400, `${hero}/${n} 头部最大空洞 ${a.headHoleBlobMax}px 异常`);
      }
      for (const y of a.yaws) {
        assert.equal(y.files.length, a.frames, `${n} 的帧数与文件数不符`);
      }
    }
  });

  test(`[${hero}] 每张帧文件都存在且是合法 PNG`, () => {
    const dir = path.join(ROOT, hero);
    const man = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
    let n = 0;
    const missing = [];
    const notPng = [];
    for (const [, a] of Object.entries(man.actions)) {
      for (const y of a.yaws) {
        for (const f of y.files) {
          n++;
          const p = path.join(dir, f);
          if (!fs.existsSync(p)) { missing.push(f); continue; }
          const fd = fs.openSync(p, 'r');
          const head = Buffer.alloc(4);
          fs.readSync(fd, head, 0, 4, 0);
          fs.closeSync(fd);
          if (head.toString('hex') !== '89504e47') notPng.push(f);
        }
      }
    }
    assert.ok(n > 40, `${hero} 帧数偏少: ${n}`);
    assert.deepEqual(missing.slice(0, 5), [], `${hero} 缺帧 ${missing.length} 张`);
    assert.deepEqual(notPng.slice(0, 5), [], `${hero} 有非 PNG 文件`);
  });
}
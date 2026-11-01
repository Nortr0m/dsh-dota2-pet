/**
 * 宿主半的离线冒烟测试：用桩替换 webServer，不联网、不需要 DSH 在跑。
 *
 *   node --test test/
 */

import test from 'node:test';
import assert from 'node:assert/strict';

import { apply, inject, normalizeAccountId, toSteam64 } from '../index.js';
import { findDota2, describeDota2 } from '../lib/find-dota.mjs';

/** 建立假 ctx，收集注册的路由 */
function boot() {
  const routes = [];
  const ctx = {
    webServer: { register: (r) => { routes.push(r); return () => {}; } },
    effect: (fn) => { fn(); }
  };
  apply(ctx);
  return routes;
}

/** 按宿主语义分发一次请求（exact 优先，其次 prefix） */
async function call(routes, url) {
  const p = url.split('?')[0];
  const hit = routes.find((r) => r.kind === 'exact' && r.path === p) ||
              routes.find((r) => r.kind === 'prefix' && p.startsWith(r.path)) ||
              routes.find((r) => r.kind === 'prefix' && r.path === p);
  if (!hit) return { status: 404, body: Buffer.alloc(0), headers: {} };
  let status = 0, headers = {}, body = null;
  const res = {
    writeHead(code, h) { status = code; headers = h || {}; },
    end(b) { body = b; }
  };
  await hit.handler({ url }, res);
  return { status, headers, body: Buffer.isBuffer(body) ? body : Buffer.from(String(body ?? '')) };
}

const routes = boot();

test('插件声明了 webServer 依赖，并注册了足够多的路由', () => {
  assert.deepEqual(inject, ['webServer']);
  assert.ok(routes.length >= 260, `注册路由数偏少: ${routes.length}`);
  const kinds = routes.reduce((m, r) => (m[r.kind] = (m[r.kind] || 0) + 1, m), {});
  assert.ok(kinds.exact > 250, '静态帧应逐文件注册精确路由');
});

test('带查询串的接口同时注册了 exact 与 prefix', () => {
  for (const p of ['/dota2-pet/api/state', '/dota2-pet/api/player', '/dota2-pet/api/window']) {
    const kinds = routes.filter((r) => r.path === p).map((r) => r.kind);
    assert.ok(kinds.includes('exact'), `${p} 缺 exact`);
    assert.ok(kinds.includes('prefix'), `${p} 缺 prefix`);
  }
});

test('账号 ID 归一化（含 Steam64，必须走 BigInt）', () => {
  assert.equal(normalizeAccountId('135700444'), 135700444);
  assert.equal(normalizeAccountId('76561198095966172'), 135700444);
  assert.equal(normalizeAccountId('https://steamcommunity.com/profiles/76561198095966172/'), 135700444);
  assert.equal(normalizeAccountId('abc'), null);
  assert.equal(normalizeAccountId(''), null);
  // 关键回归：用 Number 算会得到 …180（偏 8），查到别人账号
  assert.equal(toSteam64(135700444), '76561198095966172');
  assert.notEqual(toSteam64(135700444), '76561198095966180');
});

test('/api/state 返回本机检测结果与内置素材清单', async () => {
  const r = await call(routes, '/dota2-pet/api/state');
  assert.equal(r.status, 200);
  const j = JSON.parse(r.body.toString('utf8'));
  assert.equal(j.ok, true);
  assert.equal(typeof j.version, 'string');
  assert.ok(j.localGame && typeof j.localGame.ok === 'boolean');
  assert.ok(j.desktopWindow && typeof j.desktopWindow.supported === 'boolean');
  const slark = j.heroes.find((x) => x.hero === 'slark');
  assert.ok(slark, '内置素材里应当有 slark');
  assert.ok(slark.frameCount > 200, `内置帧数偏少: ${slark.frameCount}`);
});

test('动作帧与 manifest 都能取到，且是合法 PNG', async () => {
  const m = await call(routes, '/dota2-pet/frames/slark/manifest.json');
  assert.equal(m.status, 200);
  const man = JSON.parse(m.body.toString('utf8'));
  assert.equal(man.actions.idle_anim.frames, 24);
  assert.equal(man.actions.idle_anim.loop, true);
  assert.ok(man.actions.idle_anim.yaws[0].files.length === 24);

  const f = await call(routes, '/dota2-pet/frames/slark/' + man.actions.idle_anim.yaws[0].files[0]);
  assert.equal(f.status, 200);
  assert.equal(f.headers['Content-Type'], 'image/png');
  assert.equal(f.body.subarray(0, 4).toString('hex'), '89504e47', 'PNG 魔数不对');
  assert.ok(f.body.length > 5000, '帧文件太小，可能没烘成功');
});

test('不存在的帧返回 404 而不是别的路径', async () => {
  const r = await call(routes, '/dota2-pet/frames/slark/not_a_frame.png');
  assert.equal(r.status, 404);
});

test('非法账号在联网之前就被挡住', async () => {
  const r = await call(routes, '/dota2-pet/api/player?id=abc');
  assert.equal(r.status, 400);
  const j = JSON.parse(r.body.toString('utf8'));
  assert.equal(j.error, 'bad-account');
});

test('本机检测在给了错误路径时不会误报成功', () => {
  const found = findDota2({ manual: 'Z:\\definitely\\not\\there' });
  assert.ok(Array.isArray(found.tried));
  assert.ok(found.tried.length > 0);
  const info = describeDota2(found);
  assert.equal(typeof info.ok, 'boolean');
  if (info.ok) {
    assert.ok(info.vpk && info.gameDir, '成功时必须有 vpk 与 gameDir');
  }
  // find-dota 是纯本地逻辑：结果必须自洽（ok 与 gameDir 同真同假）
  assert.equal(info.ok, Boolean(found.gameDir));
});
/**
 * Dota 2 桌宠 · Host half
 *
 * 职责（全部在宿主进程里完成，浏览器端不碰磁盘）：
 *   1) 检索本机 Dota 2 安装目录（lib/find-dota.mjs，注册表 + Steam 库 VDF + 全盘兜底）
 *   2) 代理账号数据（OpenDota），带内存缓存 —— 浏览器直连会被 CORS 和限流卡住
 *   3) 把内置的动作帧发给前端
 *   4) Windows 上可另起一个"置顶透明桌面窗口"（WPF），桌宠就能真的浮在桌面上
 *
 * 路由注册要点（实测踩过的坑）：本环境的 prefix 路由只对"完全相同的路径"生效，
 * 所以带查询串的接口注册裸路径（exact + prefix 各一次），静态文件按每个文件逐条注册。
 */

import { appendFileSync, existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { findDota2, describeDota2 } from './lib/find-dota.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRAMES_ROOT = path.join(HERE, 'assets', 'frames');
const ICON = path.join(HERE, 'icon.svg');
const WIN_SCRIPT = path.join(HERE, 'lib', 'pet-window.ps1');
const DIAG = path.join(HERE, '_diag.log');
// 版本号只认 package.json，避免两处写死之后漂移（之前就漂过：包 0.2.0 / 代码 0.1.0）
const VERSION = (() => {
  try { return JSON.parse(readFileSync(path.join(HERE, 'package.json'), 'utf8')).version || '0.0.0'; }
  catch { return '0.0.0'; }
})();

const API = '/dota2-pet/api/';
const FRAMES = '/dota2-pet/frames/';

/* ------------------------------------------------------------------ 小工具 */

function log(obj) {
  try { appendFileSync(DIAG, JSON.stringify({ at: new Date().toISOString(), ...obj }) + '\n'); }
  catch { /* 忽略 */ }
}

function sendJson(res, data, code = 200) {
  const body = Buffer.from(JSON.stringify(data), 'utf8');
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': String(body.length),
    'Cache-Control': 'no-cache'
  });
  res.end(body);
}

function notFound(res) {
  try { res.writeHead(404); res.end('not found'); } catch { /* 忽略 */ }
}

function query(req) {
  const raw = req.url || '';
  const i = raw.indexOf('?');
  const q = {};
  if (i === -1) return q;
  for (const kv of raw.slice(i + 1).split('&')) {
    const e = kv.indexOf('=');
    if (e > 0) {
      try { q[decodeURIComponent(kv.slice(0, e))] = decodeURIComponent(kv.slice(e + 1)); }
      catch { /* 忽略坏参数 */ }
    }
  }
  return q;
}

/* -------------------------------------------------------- 本机游戏检测（带缓存） */

let detectCache = { at: 0, value: null };

function detect(manual = null, force = false) {
  const now = Date.now();
  if (!force && !manual && detectCache.value && now - detectCache.at < 30_000) {
    return detectCache.value;
  }
  const found = findDota2({ manual });
  const info = describeDota2(found);
  const value = {
    ...info,
    checkedAt: new Date().toISOString(),
    platform: process.platform,
    triedCount: (found.tried || []).length,
    hint: info.ok ? null
      : '没找到本机 Dota 2：可以在设置里手动指定路径（例如 D:\\SteamLibrary\\steamapps\\common\\dota 2 beta\\game\\dota）'
  };
  if (!manual) detectCache = { at: now, value };
  log({ tag: 'detect', ok: value.ok, gameDir: value.gameDir || null, source: value.source || null });
  return value;
}

/* ---------------------------------------------------------------- 内置素材 */

function bundledHeroes() {
  const out = [];
  let names = [];
  try { names = readdirSync(FRAMES_ROOT); } catch { return out; }
  for (const name of names) {
    const dir = path.join(FRAMES_ROOT, name);
    const manifest = path.join(dir, 'manifest.json');
    try {
      if (!statSync(dir).isDirectory() || !existsSync(manifest)) continue;
    } catch { continue; }
    let info = { hero: name, actions: 0, frameCount: 0, size: 220 };
    try {
      const m = JSON.parse(readFileSync(manifest, 'utf8'));
      info.actions = Object.keys(m.actions || {}).length;
      info.size = m.size || 220;
      info.frameCount = Object.values(m.actions || {})
        .reduce((n, a) => n + (a.yaws || []).reduce((k, y) => k + (y.files || []).length, 0), 0);
    } catch { /* manifest 坏了也不致命 */ }
    out.push(info);
  }
  return out;
}

function* frameFiles() {
  let heroes = [];
  try { heroes = readdirSync(FRAMES_ROOT); } catch { return; }
  for (const hero of heroes) {
    const dir = path.join(FRAMES_ROOT, hero);
    let files = [];
    try { files = readdirSync(dir); } catch { continue; }
    for (const f of files) {
      const ext = path.extname(f).toLowerCase();
      if (ext !== '.png' && ext !== '.json' && ext !== '.webp') continue;
      yield { route: FRAMES + hero + '/' + f, file: path.join(dir, f), ext };
    }
  }
}

/* ------------------------------------------------------------ 账号数据（OpenDota） */

const playerCache = new Map();          // key -> { at, data }
const PLAYER_TTL = 10 * 60 * 1000;

async function fetchJson(url, timeoutMs = 15000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      signal: ctl.signal,
      headers: { 'user-agent': `dsh-dota2-pet/${VERSION}` }
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } finally {
    clearTimeout(timer);
  }
}

/** 支持填 好友码/账号ID、Steam64、Steam3 或资料链接。
 *  Steam64 换算必须用 BigInt：76561197960265728 远大于 2^53，
 *  用 Number 算会丢精度（实测会差 8，把玩家指到别人账号上）。 */
const STEAM64_BASE = 76561197960265728n;

export function normalizeAccountId(input) {
  if (input == null) return null;
  const m = String(input).trim().match(/(\d{6,})/);
  if (!m) return null;
  let id;
  try { id = BigInt(m[1]); } catch { return null; }
  if (id > STEAM64_BASE) id -= STEAM64_BASE;
  if (id <= 0n || id >= 4294967296n) return null;
  return Number(id);
}

export function toSteam64(accountId) {
  return String(BigInt(accountId) + STEAM64_BASE);
}

async function playerData(accountId, days) {
  const key = accountId + ':' + days;
  const hit = playerCache.get(key);
  if (hit && Date.now() - hit.at < PLAYER_TTL) return { ...hit.data, cached: true };

  const base = 'https://api.opendota.com/api';
  const [profile, heroes, wl, recent] = await Promise.allSettled([
    fetchJson(`${base}/players/${accountId}`),
    fetchJson(`${base}/players/${accountId}/heroes?date=${days}`),
    fetchJson(`${base}/players/${accountId}/wl?date=${days}`),
    fetchJson(`${base}/players/${accountId}/recentMatches`)
  ]);
  const val = (r, d) => (r.status === 'fulfilled' ? r.value : d);

  const p = val(profile, null);
  if (!p || !p.profile) {
    const err = new Error('账号查不到（OpenDota 没有这个 account_id）');
    err.code = 'unknown-account';
    throw err;
  }
  const hs = Array.isArray(val(heroes, [])) ? val(heroes, []) : [];
  hs.sort((a, b) => (b.games || 0) - (a.games || 0));

  const data = {
    ok: true,
    accountId,
    steam64: toSteam64(accountId),
    days,
    profile: {
      name: p.profile.personaname,
      avatar: p.profile.avatarfull || p.profile.avatar,
      country: p.profile.loccountrycode || null,
      rankTier: p.rank_tier || null,
      leaderboard: p.leaderboard_rank || null
    },
    winloss: val(wl, null),
    topHeroes: hs.slice(0, 12).map((h) => ({
      heroId: h.hero_id, games: h.games, win: h.win,
      winrate: h.games ? Math.round((h.win / h.games) * 1000) / 10 : null,
      lastPlayed: h.last_played || null
    })),
    recent: (Array.isArray(val(recent, [])) ? val(recent, []) : []).slice(0, 20).map((m) => ({
      heroId: m.hero_id, win: m.radiant_win === (m.player_slot < 128),
      kills: m.kills, deaths: m.deaths, assists: m.assists,
      startTime: m.start_time
    })),
    fetchedAt: new Date().toISOString()
  };
  playerCache.set(key, { at: Date.now(), data });
  return data;
}

/* ------------------------------------------------- 桌面窗口（Windows 置顶透明） */

let petWindow = null;      // { child, startedAt, args }

/**
 * 桌面窗口的中文标签。
 * PowerShell 5.1 会把无 BOM 的 .ps1 按 ANSI 读，所以脚本源码必须保持纯 ASCII；
 * 中文一律走这个 UTF-8 数据文件，脚本只负责读数据（读取时显式指定 UTF8 编码）。
 */
const PET_LABELS = {
  idle: '待机', move: '移动', attack: '攻击', cast: '施法', skill: '技能',
  hit: '受击', death: '死亡', victory: '胜利', spawn: '出生/传送', show: '展示/嘲讽',
  other: '动作', action: '动作', scaleUp: '放大', scaleDown: '缩小',
  quit: '退出桌宠', title: 'Dota 2 桌宠'
};

function labelFile() {
  const file = path.join(HERE, '_pet_labels.json');
  try {
    writeFileSync(file, JSON.stringify(PET_LABELS, null, 1), 'utf8');
  } catch (e) {
    log({ tag: 'labels', error: String(e && e.message) });
    return null;
  }
  return file;
}

function windowStatus() {
  const alive = petWindow && petWindow.child && petWindow.child.exitCode === null;
  return {
    supported: process.platform === 'win32',
    running: Boolean(alive),
    startedAt: alive ? petWindow.startedAt : null,
    pid: alive ? petWindow.child.pid : null,
    reason: process.platform === 'win32' ? null : '独立桌面窗口目前只支持 Windows'
  };
}

function startWindow(q) {
  if (process.platform !== 'win32') return { ok: false, error: 'not-windows' };
  const st = windowStatus();
  if (st.running) return { ok: true, already: true, ...st };

  const hero = (q.hero || 'slark').replace(/[^a-z0-9_]/gi, '');
  const dir = path.join(FRAMES_ROOT, hero);
  if (!existsSync(path.join(dir, 'manifest.json'))) {
    return { ok: false, error: 'no-frames', hero };
  }
  const labels = labelFile();
  const args = [
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
    '-File', WIN_SCRIPT,
    '-Frames', dir,
    '-Action', q.action || 'idle_anim',
    '-Scale', String(Math.max(0.5, Math.min(3, Number(q.scale) || 1))),
    '-Left', String(Number(q.left) || 0),
    '-Top', String(Number(q.top) || 0)
  ];
  if (labels) args.push('-Labels', labels);
  try {
    const child = spawn('powershell.exe', args, { detached: true, stdio: 'ignore', windowsHide: true });
    child.unref();
    petWindow = { child, startedAt: new Date().toISOString(), args, hero };
    log({ tag: 'window', action: 'start', pid: child.pid, hero });
    return { ok: true, pid: child.pid, ...windowStatus() };
  } catch (e) {
    log({ tag: 'window', action: 'start', error: String(e && e.message) });
    return { ok: false, error: String(e && e.message) };
  }
}

function stopWindow() {
  const alive = petWindow && petWindow.child && petWindow.child.exitCode === null;
  if (alive) {
    try { petWindow.child.kill(); } catch { /* 忽略 */ }
  }
  petWindow = null;
  log({ tag: 'window', action: 'stop' });
  return { ok: true, ...windowStatus() };
}

/* ------------------------------------------------------------------ 插件入口 */

export const inject = ['webServer'];

export function apply(ctx) {
  const webServer = ctx.webServer;
  if (!webServer || typeof webServer.register !== 'function') {
    log({ tag: 'apply', error: 'webServer 不可用' });
    return;
  }

  ctx.effect(() => {
    const disposers = [];
    const claim = (p, handler, kinds = ['exact']) => {
      for (const kind of kinds) {
        try { disposers.push(webServer.register({ kind, path: p, handler })); }
        catch { /* 已被占用 */ }
      }
    };

    // ---- 状态：本机游戏检测 + 内置英雄
    const stateHandler = (req, res) => {
      try {
        const q = query(req);
        const det = detect(q.path || null, q.force === '1');
        const heroes = bundledHeroes();
        sendJson(res, {
          ok: true, version: VERSION, localGame: det, heroes,
          desktopWindow: windowStatus(),
          serverTime: new Date().toISOString()
        });
      } catch (e) { sendJson(res, { ok: false, error: String(e && e.message) }, 500); }
    };
    claim(API + 'state', stateHandler, ['exact', 'prefix']);
    claim('/dota2-pet/state', stateHandler, ['exact']);

    // ---- 账号数据
    const playerHandler = async (req, res) => {
      try {
        const q = query(req);
        const id = normalizeAccountId(q.id || q.account || q.steam || q.steam64);
        if (!id) { sendJson(res, { ok: false, error: 'bad-account', hint: '填好友码/账号 ID 或 Steam64' }, 400); return; }
        const days = Math.max(1, Math.min(365, Number(q.days) || 90));
        sendJson(res, await playerData(id, days));
      } catch (e) {
        sendJson(res, { ok: false, error: String(e && e.code || e && e.message || e) }, 502);
      }
    };
    claim(API + 'player', playerHandler, ['exact', 'prefix']);

    // ---- 桌面窗口开关
    const windowHandler = (req, res) => {
      try {
        const q = query(req);
        if (q.action === 'start') sendJson(res, startWindow(q));
        else if (q.action === 'stop') sendJson(res, stopWindow());
        else sendJson(res, { ok: true, ...windowStatus(), heroes: bundledHeroes().map((x) => x.hero) });
      } catch (e) { sendJson(res, { ok: false, error: String(e && e.message) }, 500); }
    };
    claim(API + 'window', windowHandler, ['exact', 'prefix']);

    // ---- 内置动作帧（每个文件一条 exact 路由，绕开 prefix 的坑）
    let frameCount = 0;
    for (const { route, file, ext } of frameFiles()) {
      const type = ext === '.json' ? 'application/json; charset=utf-8'
        : ext === '.webp' ? 'image/webp' : 'image/png';
      claim(route, (req, res) => {
        try {
          const buf = statSync(file).isFile() ? readFileSync(file) : null;
          if (!buf) { notFound(res); return; }
          res.writeHead(200, {
            'Content-Type': type,
            'Content-Length': String(buf.length),
            'Cache-Control': 'public, max-age=604800'
          });
          res.end(buf);
        } catch { notFound(res); }
      });
      frameCount++;
    }

    // ---- 图标
    claim('/dota2-pet/icon.svg', (req, res) => {
      try {
        const buf = readFileSync(ICON);
        res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Content-Length': String(buf.length) });
        res.end(buf);
      } catch { notFound(res); }
    });

    log({ tag: 'apply', version: VERSION, routes: disposers.length, frameRoutes: frameCount });

    // 启动自检放到事件循环之后：注册表查询/全盘扫描不阻塞 DSH 启动
    setTimeout(() => {
      try {
        const det = detect();
        log({ tag: 'boot', gameOk: det.ok, gameDir: det.gameDir || null, source: det.source || null });
      } catch (e) {
        log({ tag: 'boot', error: String(e && e.message) });
      }
    }, 1500);

    return () => {
      for (const d of disposers) { try { d(); } catch { /* 已移除 */ } }
    };
  });
}
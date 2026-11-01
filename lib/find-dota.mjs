/**
 * 本机 Dota 2 安装目录检索（跨平台、无第三方依赖）。
 *
 * 检索顺序（先便宜后昂贵，任何一个命中即返回）：
 *   1. 调用方显式给的路径（用户在设置里手填）
 *   2. Windows 注册表：HKCU\Software\Valve\Steam\SteamPath
 *                      HKLM\SOFTWARE\WOW6432Node\Valve\Steam\InstallPath
 *                      HKLM\SOFTWARE\Valve\Steam\InstallPath
 *   3. 常见 Steam 安装位置 × 全盘符（社区通用做法，不依赖注册表）
 *   4. 每个 Steam 根目录的 steamapps\libraryfolders.vdf 与 config\libraryfolders.vdf
 *      （Steam 自己维护的库列表，能覆盖装在任意盘/任意目录的库）
 *   5. 上述每个库目录下的 steamapps\common\dota 2 beta（并用 appmanifest_570.acf 交叉验证）
 *
 * 判定标准只有一条：<gameDir>\pak01_dir.vpk 真的能打开。
 */

import { existsSync, readFileSync, statSync, readdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { pathToFileURL } from 'node:url';

const REL = path.join('steamapps', 'common', 'dota 2 beta');
const GAME_REL = path.join(REL, 'game', 'dota');
const VPK = 'pak01_dir.vpk';

/** 明确的失败原因，便于在界面上一句话说清"卡在哪一步"。 */
function probeGameDir(dir, source) {
  if (!dir) return null;
  const normalized = path.normalize(dir);
  for (const candidate of [normalized,
                           path.join(normalized, 'game', 'dota'),
                           path.join(normalized, 'dota')]) {
    try {
      if (statSync(path.join(candidate, VPK)).isFile()) {
        return { gameDir: candidate, vpk: path.join(candidate, VPK), source };
      }
    } catch { /* 继续 */ }
  }
  return null;
}

function registrySteamRoots() {
  const out = [];
  if (process.platform !== 'win32') return out;
  const queries = [
    ['HKCU\\Software\\Valve\\Steam', 'SteamPath'],
    ['HKLM\\SOFTWARE\\WOW6432Node\\Valve\\Steam', 'InstallPath'],
    ['HKLM\\SOFTWARE\\Valve\\Steam', 'InstallPath']
  ];
  for (const [key, name] of queries) {
    try {
      const res = execFileSync('reg', ['query', key, '/v', name],
                               { encoding: 'utf8', windowsHide: true, timeout: 4000 });
      const m = res.match(/REG_SZ\s+(\S.*?)\s*$/m);
      if (m) out.push({ root: m[1].replace(/\//g, '\\'), source: 'registry:' + name });
    } catch { /* 键不存在 */ }
  }
  return out;
}

function driveLetters() {
  const out = [];
  const isWin = process.platform === 'win32';
  for (const letter of 'CDEFGHIJKLMNOPQRSTUVWXYZ') {
    out.push(letter + ':\\');
    if (!isWin) out.push('/mnt/' + letter.toLowerCase() + '/');
  }
  return out;
}

function commonSteamRoots() {
  const subs = [
    'Steam', 'SteamLibrary', 'Games\\Steam', 'Games/Steam',
    'Program Files (x86)\\Steam', 'Program Files\\Steam',
    'Program Files (x86)/Steam', 'Program Files/Steam'
  ];
  const roots = [];
  for (const drive of driveLetters()) {
    for (const sub of subs) roots.push(path.join(drive, sub));
  }
  // 非 Windows 的常见位置
  roots.push(path.join(os.homedir(), '.steam', 'steam'),
             path.join(os.homedir(), '.local', 'share', 'Steam'),
             path.join(os.homedir(), 'Library', 'Application Support', 'Steam'));
  return roots;
}

/** 从 libraryfolders.vdf 里读出所有库路径（Steam 自己写的，最权威）。 */
function librariesFromVdf(steamRoot) {
  const out = [];
  for (const rel of [path.join('steamapps', 'libraryfolders.vdf'),
                     path.join('config', 'libraryfolders.vdf')]) {
    const file = path.join(steamRoot, rel);
    try {
      const text = readFileSync(file, 'utf8');
      const re = /"path"\s+"([^"]+)"/g;
      let m;
      while ((m = re.exec(text))) out.push(m[1].replace(/\\\\/g, '\\'));
    } catch { /* 没有这个文件 */ }
  }
  return out;
}

/** 库目录里是否有 Dota 2 的 appmanifest（appid 570）。 */
function hasDotaManifest(lib) {
  try {
    return existsSync(path.join(lib, 'steamapps', 'appmanifest_570.acf'));
  } catch { return false; }
}

export function findDota2({ manual = null, log = () => {} } = {}) {
  const tried = [];
  const hit = (r) => { if (r) log(`命中：${r.gameDir}  (${r.source})`); return r; };

  // 1. 用户手填
  if (manual) {
    tried.push(['manual', manual]);
    const r = probeGameDir(manual, 'manual');
    if (r) return { ...r, tried };
  }

  // 2 + 3. Steam 根目录（注册表优先，其次磁盘常见位置）
  const roots = [];
  const seenRoot = new Set();
  const addRoot = (root, source) => {
    if (!root) return;
    const key = root.toLowerCase();
    if (seenRoot.has(key)) return;
    seenRoot.add(key);
    roots.push({ root, source });
  };
  for (const r of registrySteamRoots()) addRoot(r.root, r.source);
  for (const root of commonSteamRoots()) {
    try {
      if (existsSync(path.join(root, 'steamapps'))) addRoot(root, 'scan');
    } catch { /* 无权限 */ }
  }

  // 4. 展开 Steam 库列表
  const libs = [];
  const seenLib = new Set();
  for (const { root, source } of roots) {
    tried.push([source, root]);
    const direct = probeGameDir(path.join(root, REL), source) ||
                   probeGameDir(path.join(root, 'steamapps', 'common', 'dota 2 beta'), source);
    if (direct) return { ...direct, tried };
    for (const lib of librariesFromVdf(root)) {
      const key = lib.toLowerCase();
      if (seenLib.has(key)) continue;
      seenLib.add(key);
      libs.push(lib);
      tried.push(['library.vdf', lib]);
      const r = probeGameDir(path.join(lib, REL), 'library.vdf') ||
                probeGameDir(path.join(lib, 'steamapps', 'common', 'dota 2 beta'), 'library.vdf');
      if (r) return { ...r, tried };
    }
    // 该 Steam 根目录自身也是一个库
    const self = path.join(root, REL);
    tried.push(['steam.root', self]);
    const r = probeGameDir(self, 'steam.root') ||
              probeGameDir(path.join(root, 'steamapps', 'common', 'dota 2 beta'), 'steam.root');
    if (r) return { ...r, tried };
  }

  // 5. 兜底：直接扫全盘 steamapps\common\dota 2 beta
  for (const drive of driveLetters()) {
    for (const mid of ['steamapps', path.join('SteamLibrary', 'steamapps'),
                       path.join('Games', 'Steam', 'steamapps')]) {
      const dir = path.join(drive, mid, 'common', 'dota 2 beta');
      tried.push(['drive.scan', dir]);
      const r = probeGameDir(dir, 'drive.scan');
      if (r) return { ...r, tried };
    }
  }
  return { gameDir: null, vpk: null, source: null, tried };
}

/** 顺带把库信息（是否 Steam 版、库根目录）一起返回，便于界面展示。 */
export function describeDota2(found) {
  if (!found || !found.gameDir) return { ok: false, reason: 'not-found', tried: found?.tried || [] };
  const gameDir = found.gameDir;
  const library = gameDir.split(/[\\/]steamapps[\\/]/)[0] || null;
  let size = 0;
  try { size = statSync(found.vpk).size; } catch { /* 忽略 */ }
  return {
    ok: true,
    gameDir,
    library,
    vpk: found.vpk,
    vpkBytes: size,
    steam: Boolean(library) && hasDotaManifest(library),
    source: found.source,
    triedCount: found.tried.length
  };
}

export function listLibraries(found) {
  const out = new Set();
  for (const [source, p] of found?.tried || []) {
    if (source === 'library.vdf' || source === 'steam.root') out.add(p);
  }
  return [...out];
}

/* CLI：node find-dota.mjs [手填路径] */
const isMain = (() => {
  try { return import.meta.url === pathToFileURL(process.argv[1]).href; } catch { return false; }
})();
if (isMain) {
  const manual = process.argv[2] || null;
  const t0 = Date.now();
  const found = findDota2({ manual, log: (m) => console.log('  ' + m) });
  const info = describeDota2(found);
  console.log(JSON.stringify({ ...info, ms: Date.now() - t0 }, null, 1));
  if (!info.ok) {
    console.log('尝试过的路径（前 12 条）：');
    for (const [s, p] of (found.tried || []).slice(0, 12)) console.log('  [' + s + '] ' + p);
  }
}
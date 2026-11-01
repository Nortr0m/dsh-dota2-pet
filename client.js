/* ==========================================================================
   Dota 2 桌宠 — Client half
   - 浮层桌宠（shell.overlay 插槽）：动作帧由 Host 半从 /dota2-pet/frames/ 提供
   - 账号配置（设置 → 桌宠）：OpenDota 数据经 Host 半代理，浏览器端不直连
   - Windows 上可另起置顶透明桌面窗口（Host 半用 WPF 拉起）
   ========================================================================== */

window.__ModuleLoader__.load({
  id: 'dsh-dota2-pet',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const API = '/dota2-pet/api/';
    const FRAMES = '/dota2-pet/frames/';
    const LS_KEY = 'dota2pet.v1';

    /* ------------------------------------------------------------ 配置存储 */

    const DEFAULT_PREFS = {
      enabled: true, hero: 'slark', scale: 1, x: null, y: null,
      action: 'idle_anim', account: '', days: 90, mmrManual: '', bubble: true
    };

    function loadPrefs() {
      try {
        const raw = window.localStorage.getItem(LS_KEY);
        if (!raw) return { ...DEFAULT_PREFS };
        return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
      } catch { return { ...DEFAULT_PREFS }; }
    }
    function savePrefs(p) {
      try { window.localStorage.setItem(LS_KEY, JSON.stringify(p)); } catch { /* 忽略 */ }
    }

    let prefs = loadPrefs();
    const prefSubs = new Set();
    function setPref(patch) {
      prefs = { ...prefs, ...patch };
      savePrefs(prefs);
      prefSubs.forEach((fn) => { try { fn(prefs); } catch { /* 忽略 */ } });
    }
    function usePrefs() {
      const [v, setV] = React.useState(prefs);
      React.useEffect(() => {
        const fn = (p) => setV(p);
        prefSubs.add(fn);
        return () => prefSubs.delete(fn);
      }, []);
      return v;
    }

    /* ------------------------------------------------------------ 账号数据 */

    const RANK_MEDALS = {
      1: '先驱', 2: '卫士', 3: '十字军', 4: '执政官',
      5: '传奇', 6: '远古', 7: '神圣', 8: '不朽'
    };
    const ROMAN = ['', 'I', 'II', 'III', 'IV', 'V'];
    function rankLabel(tier) {
      if (!tier) return '未校准';
      const medal = Math.floor(tier / 10), star = tier % 10;
      const name = RANK_MEDALS[medal];
      if (!name) return '未校准';
      if (medal >= 8) return '不朽';                    // 不朽不再分星
      return name + ' ' + (ROMAN[star] || star);
    }
    function num(v) { return typeof v === 'number' && isFinite(v) ? v : null; }

    /* 英雄头像：优先本机游戏（Host 半检测到就说明可用），退回 Steam CDN */
    const HERO_SLUG = {
      1: 'antimage', 2: 'axe', 3: 'bane', 4: 'bloodseeker', 5: 'crystal_maiden',
      6: 'drow_ranger', 7: 'earthshaker', 8: 'juggernaut', 9: 'mirana', 10: 'morphling',
      11: 'nevermore', 12: 'phantom_lancer', 13: 'puck', 14: 'pudge', 15: 'razor',
      16: 'sand_king', 17: 'storm_spirit', 18: 'sven', 19: 'tiny', 20: 'vengefulspirit',
      21: 'windrunner', 22: 'zuus', 23: 'kunkka', 25: 'lina', 26: 'lion',
      27: 'shadow_shaman', 28: 'slardar', 29: 'tidehunter', 30: 'witch_doctor',
      31: 'lich', 32: 'riki', 33: 'enigma', 34: 'tinker', 35: 'sniper',
      36: 'necrolyte', 37: 'warlock', 38: 'beastmaster', 39: 'queenofpain', 40: 'venomancer',
      41: 'faceless_void', 42: 'skeleton_king', 43: 'death_prophet', 44: 'phantom_assassin',
      45: 'pugna', 46: 'templar_assassin', 47: 'viper', 48: 'luna', 49: 'dragon_knight',
      50: 'dazzle', 51: 'rattletrap', 52: 'leshrac', 53: 'furion', 54: 'life_stealer',
      55: 'dark_seer', 56: 'clinkz', 57: 'omniknight', 58: 'enchantress', 59: 'huskar',
      60: 'night_stalker', 61: 'broodmother', 62: 'bounty_hunter', 63: 'weaver',
      64: 'jakiro', 65: 'batrider', 66: 'chen', 67: 'spectre', 68: 'ancient_apparition',
      69: 'doom_bringer', 70: 'ursa', 71: 'spirit_breaker', 72: 'gyrocopter',
      73: 'alchemist', 74: 'invoker', 75: 'silencer', 76: 'obsidian_destroyer',
      77: 'lycan', 78: 'brewmaster', 79: 'shadow_demon', 80: 'lone_druid',
      81: 'chaos_knight', 82: 'meepo', 83: 'treant', 84: 'ogre_magi', 85: 'undying',
      86: 'rubick', 87: 'disruptor', 88: 'nyx_assassin', 89: 'naga_siren',
      90: 'keeper_of_the_light', 91: 'wisp', 92: 'visage', 93: 'slark', 94: 'medusa',
      95: 'troll_warlord', 96: 'centaur', 97: 'magnataur', 98: 'shredder',
      99: 'bristleback', 100: 'tusk', 101: 'skywrath_mage', 102: 'abaddon',
      103: 'elder_titan', 104: 'legion_commander', 105: 'techies', 106: 'ember_spirit',
      107: 'earth_spirit', 108: 'abyssal_underlord', 109: 'terrorblade', 110: 'phoenix',
      111: 'oracle', 112: 'winter_wyvern', 113: 'arc_warden', 114: 'monkey_king',
      119: 'dark_willow', 120: 'pangolier', 121: 'grimstroke', 123: 'hoodwink',
      126: 'void_spirit', 128: 'snapfire', 129: 'mars', 131: 'ringmaster',
      135: 'dawnbreaker', 136: 'marci', 137: 'primal_beast', 138: 'muerta',
      145: 'kez', 155: 'ringmaster'
    };
    function heroName(id) {
      const names = {
        14: '帕吉', 88: '司夜刺客', 93: '斯拉克', 84: '食人魔魔法师', 74: '祈求者',
        8: '主宰', 5: '水晶室女', 1: '敌法师', 11: '影魔', 41: '虚空假面'
      };
      return names[id] || ('英雄 #' + id);
    }
    function heroPortrait(id) {
      const slug = HERO_SLUG[id];
      if (!slug) return null;
      return 'https://cdn.cloudflare.steamstatic.com/apps/dota2/images/dota_react/heroes/' + slug + '.png';
    }

    async function getJSON(url) {
      const r = await fetch(url);
      const j = await r.json().catch(() => null);
      if (!r.ok || !j || j.ok === false) {
        const err = new Error((j && (j.hint || j.error)) || ('HTTP ' + r.status));
        err.payload = j;
        throw err;
      }
      return j;
    }

    /* --------------------------------------------------------------- 台词 */

    const LINES = [
      '黑暗……在涌动。', '你闻到血腥味了吗？', '别跑，我不会咬人的。',
      '深海的猎物，从来跑不掉。', '我闻到了恐惧。', '又一个想抓我的？',
      '小心点，这地方很暗。', '暗影之舞，开始了。'
    ];

    /* ==================================================================== */
    /*  浮层桌宠                                                            */
    /* ==================================================================== */

    function PetOverlay() {
      const p = usePrefs();
      const [state, setState] = React.useState(null);
      const [manifest, setManifest] = React.useState(null);
      const [action, setAction] = React.useState(p.action || 'idle_anim');
      const [frame, setFrame] = React.useState(0);
      const [line, setLine] = React.useState('');
      const [menu, setMenu] = React.useState(null);
      const imgCache = React.useRef({});
      const dragRef = React.useRef(null);
      const containerRef = React.useRef(null);

      const hero = p.hero || 'slark';

      React.useEffect(() => {
        let alive = true;
        getJSON(API + 'state').then((s) => { if (alive) setState(s); }).catch(() => {});
        return () => { alive = false; };
      }, []);

      React.useEffect(() => {
        let alive = true;
        setManifest(null);
        getJSON(FRAMES + hero + '/manifest.json')
          .then((m) => { if (alive) setManifest(m); })
          .catch(() => { if (alive) setManifest({ actions: {} }); });
        return () => { alive = false; };
      }, [hero]);

      /* 该英雄的待机动作名：各英雄动作名不同，不能写死 idle_anim */
      const idleName = React.useMemo(() => {
        const acts = (manifest && manifest.actions) || {};
        const names = Object.keys(acts);
        if (!names.length) return null;
        return names.find((n) => acts[n].category === '待机')
            || (acts['idle_anim'] ? 'idle_anim' : names[0]);
      }, [manifest]);

      const clip = React.useMemo(() => {
        if (!manifest || !manifest.actions) return null;
        const acts = manifest.actions;
        const name = acts[action] ? action : idleName;
        const a = acts[name];
        if (!a) return null;
        const yaw = a.yaws && a.yaws[0];
        if (!yaw || !yaw.files || !yaw.files.length) return null;
        return { name, files: yaw.files, fps: a.fps || 12, loop: Boolean(a.loop) };
      }, [manifest, action, idleName]);

      /* 预加载当前动作的所有帧 */
      React.useEffect(() => {
        if (!clip) return;
        clip.files.forEach((f) => {
          const url = FRAMES + hero + '/' + f;
          if (imgCache.current[url]) return;
          const im = new window.Image();
          im.src = url;
          imgCache.current[url] = im;
        });
      }, [clip, hero]);

      /* 播放：非循环动作播完回到 idle */
      React.useEffect(() => {
        if (!clip || !p.enabled) return;
        setFrame(0);
        const step = Math.max(40, Math.round(1000 / (clip.fps || 12)));
        let i = 0;
        let stop = false;
        const timer = window.setInterval(() => {
          if (stop || document.hidden) return;
          i += 1;
          if (i >= clip.files.length) {
            if (clip.loop) i = 0;
            else {
              window.clearInterval(timer);
              if (idleName) setAction(idleName);   // 播完回到该英雄自己的待机动作
              return;
            }
          }
          setFrame(i);
        }, step);
        return () => { stop = true; window.clearInterval(timer); };
      }, [clip, p.enabled, idleName]);

      /* 拖动 */
      function onDown(e) {
        if (e.button !== 0) return;
        const box = containerRef.current.getBoundingClientRect();
        dragRef.current = { dx: e.clientX - box.left, dy: e.clientY - box.top, moved: false };
        window.addEventListener('mousemove', onMove);
        window.addEventListener('mouseup', onUp);
      }
      function onMove(e) {
        const d = dragRef.current;
        if (!d) return;
        if (Math.abs(e.clientX - d.dx) > 3 || Math.abs(e.clientY - d.dy) > 3) d.moved = true;
        if (d.moved) setPref({ x: e.clientX - d.dx, y: e.clientY - d.dy });
      }
      function onUp() {
        const d = dragRef.current;
        dragRef.current = null;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        if (d && !d.moved) speak();
      }
      function speak() {
        if (!manifest || !manifest.actions) return;
        // 台词配一个"有表现力"的动作：优先技能/施法/攻击/胜利，各英雄动作名不同所以按分类挑
        const acts = manifest.actions;
        const fun = ['技能', '施法', '攻击', '胜利'];
        const pool = Object.keys(acts).filter((n) => fun.indexOf(acts[n].category) >= 0 && !acts[n].loop);
        const has = pool.length ? pool : Object.keys(acts);
        if (has.length) setAction(has[Math.floor(Math.random() * has.length)]);
        if (p.bubble) {
          setLine(LINES[Math.floor(Math.random() * LINES.length)]);
          window.setTimeout(() => setLine(''), 2600);
        }
      }
      function onWheel(e) {
        e.preventDefault();
        setPref({ scale: Math.max(0.5, Math.min(2.5, Math.round((p.scale + (e.deltaY < 0 ? 0.1 : -0.1)) * 10) / 10)) });
      }

      if (!p.enabled) return null;

      const size = (manifest && manifest.size ? manifest.size : 220) * (p.scale || 1);
      const style = { position: 'fixed', width: size, height: size, zIndex: 4000, cursor: 'grab',
                      userSelect: 'none', touchAction: 'none' };
      if (p.x == null || p.y == null) { style.right = 48; style.bottom = 72; }
      else { style.left = Math.max(0, Math.min(window.innerWidth - size, p.x)); style.top = Math.max(0, Math.min(window.innerHeight - size, p.y)); }

      const src = clip ? FRAMES + hero + '/' + clip.files[frame % clip.files.length] : null;

      return h('div', { className: 'd2p-root' },
        src ? h('div', {
          ref: containerRef, style, onMouseDown: onDown, onWheel,
          onContextMenu: (e) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }); },
          onDoubleClick: () => { setPref({ enabled: false }); }
        }, h('img', { src, alt: '桌宠', draggable: false,
                     style: { width: '100%', height: '100%', pointerEvents: 'none' } })) : null,
        line ? h('div', {
          style: {
            position: 'fixed', left: (p.x == null ? window.innerWidth - 320 : p.x),
            top: (p.y == null ? window.innerHeight - 140 : Math.max(10, p.y - 54)),
            maxWidth: 260, padding: '8px 12px', borderRadius: 10,
            background: 'rgba(20,13,7,0.92)', color: '#f0cd82',
            border: '1px solid #6b4a20', fontSize: 13, zIndex: 4001, pointerEvents: 'none'
          }
        }, line) : null,
        menu ? h('div', {
          style: {
            position: 'fixed', left: menu.x, top: menu.y, zIndex: 4002,
            background: 'rgba(20,13,7,0.96)', border: '1px solid #6b4a20',
            borderRadius: 8, padding: 6, color: '#e8d4ad', fontSize: 12, minWidth: 150
          },
          onMouseLeave: () => setMenu(null)
        },
          menuItem('说句话', () => { setMenu(null); speak(); }),
          // 动作列表按当前英雄的 manifest 动态生成（各英雄动作名不同，不能写死）
          ...actionItems(manifest, clip && clip.name, (n) => { setMenu(null); setAction(n); }),
          menuItem('放大', () => { setMenu(null); setPref({ scale: Math.min(2.5, p.scale + 0.2) }); }),
          menuItem('缩小', () => { setMenu(null); setPref({ scale: Math.max(0.5, p.scale - 0.2) }); }),
          menuItem('贴到桌面（Windows）', () => { setMenu(null); windowStart({ hero, scale: p.scale, action: clip && clip.name }); }),
          menuItem('藏起来', () => { setMenu(null); setPref({ enabled: false }); })
        ) : null
      );
    }

    /** 把该英雄的动作渲染成菜单项：显示分类，重名时补动作名 */
    function actionItems(manifest, current, onPick, max) {
      const acts = (manifest && manifest.actions) || {};
      const names = Object.keys(acts).filter((n) => n.slice(0, 2) !== '@@').slice(0, max || 9);
      const seen = {};
      return names.map((n) => {
        const cat = acts[n].category || n;
        seen[cat] = (seen[cat] || 0) + 1;
        const label = (current === n ? '● ' : '') + cat +
          (seen[cat] > 1 || !acts[n].category ? '（' + n.replace(/^@/, '').replace(/_anim$/, '') + '）' : '');
        return menuItem(label, () => onPick(n));
      });
    }

    function menuItem(label, onClick) {
      return h('div', {
        key: label,
        onClick,
        style: { padding: '6px 10px', borderRadius: 6, cursor: 'pointer' },
        onMouseEnter: (e) => { e.currentTarget.style.background = 'rgba(240,205,130,0.14)'; },
        onMouseLeave: (e) => { e.currentTarget.style.background = 'transparent'; }
      }, label);
    }

    async function windowStart(opts) {
      try {
        const q = new window.URLSearchParams({
          action: 'start', hero: opts.hero || 'slark',
          scale: String(opts.scale || 1), action: opts.action || 'idle_anim'
        });
        return await getJSON(API + 'window?' + q.toString());
      } catch (e) { return { ok: false, error: String(e && e.message) }; }
    }
    async function windowStop() {
      try { return await getJSON(API + 'window?action=stop'); }
      catch (e) { return { ok: false, error: String(e && e.message) }; }
    }

    /* ==================================================================== */
    /*  设置区：账号 + 本机游戏 + 桌宠                                      */
    /* ==================================================================== */

    const box = {
      background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.12)',
      borderRadius: 10, padding: 14, marginBottom: 12
    };
    const input = {
      width: '100%', padding: '7px 10px', borderRadius: 6,
      border: '1px solid rgba(255,255,255,0.18)', background: 'rgba(0,0,0,0.25)',
      color: 'inherit', font: 'inherit', fontSize: 13
    };
    const btn = {
      padding: '7px 14px', borderRadius: 7, border: '1px solid #6b4a20',
      background: 'rgba(240,205,130,0.12)', color: '#f0cd82', cursor: 'pointer', fontSize: 13
    };
    const hint = { fontSize: 12, opacity: 0.66, lineHeight: 1.6 };

    function SettingsSection() {
      const p = usePrefs();
      const [state, setState] = React.useState(null);
      const [account, setAccount] = React.useState(p.account || '');
      const [days, setDays] = React.useState(String(p.days || 90));
      const [data, setData] = React.useState(null);
      const [err, setErr] = React.useState('');
      const [busy, setBusy] = React.useState(false);
      const [win, setWin] = React.useState(null);
      const [manual, setManual] = React.useState('');

      function refreshState(manualPath) {
        const url = API + 'state' + (manualPath ? ('?force=1&path=' + encodeURIComponent(manualPath)) : '');
        getJSON(url).then(setState).catch(() => {});
      }
      React.useEffect(() => { refreshState(); }, []);

      async function load() {
        setBusy(true); setErr('');
        try {
          const d = await getJSON(API + 'player?id=' + encodeURIComponent(account) + '&days=' + days);
          setData(d);
          setPref({ account: String(account), days: Number(days) });
        } catch (e) {
          setErr(String(e && e.message));
          setData(null);
        } finally { setBusy(false); }
      }

      const local = state && state.localGame;
      const heroes = (state && state.heroes) || [];
      const bundled = {};
      heroes.forEach((x) => { bundled[x.hero] = x; });

      const localBlock = !local ? null : (local.ok
        ? h('div', null,
            h('div', { style: { color: '#9fd08a' } }, '✓ 已找到：' + local.gameDir),
            h('div', { style: hint }, '判定依据 ' + local.source + ' · pak01_dir.vpk ' +
              Math.round((local.vpkBytes || 0) / 1048576) + ' MB · ' +
              (local.steam ? 'Steam 版' : '非 Steam 安装')))
        : h('div', null,
            h('div', { style: { color: '#e0a97a' } }, '✗ 没找到本机 Dota 2'),
            h('div', { style: hint }, local.hint || ''),
            h('div', { style: hint }, '已尝试 ' + (local.triedCount || 0) + ' 个位置')));

      return h('div', { style: { fontSize: 13 } },
        h('div', { style: { fontSize: 15, fontWeight: 600, marginBottom: 4 } }, 'DOTA2 桌宠'),
        h('div', { style: hint }, '桌宠的动作来自本机 Dota 2 的模型（离线烘成帧，内置在插件里）；账号数据经宿主半代理 OpenDota。'),

        /* --- 账号 --- */
        h('div', { style: box },
          h('div', { style: { fontWeight: 600, marginBottom: 8 } }, '账号'),
          h('div', { style: hint }, '填好友码（账号 ID）或 Steam64 都行，不需要 API key。'),
          h('div', { style: { display: 'flex', gap: 8, marginTop: 8 } },
            h('input', {
              style: input, value: account, placeholder: '例如 135700444 或 76561198095966172',
              onChange: (e) => setAccount(e.target.value)
            }),
            h('select', {
              style: { ...input, width: 120 }, value: days, onChange: (e) => setDays(e.target.value)
            }, [30, 90, 180, 365, 1000000].map((d) =>
              h('option', { key: d, value: String(d) }, d >= 1000000 ? '全部' : ('近 ' + d + ' 天')))),
            h('button', { style: btn, onClick: load, disabled: busy }, busy ? '查询中…' : '查询')
          ),
          err ? h('div', { style: { color: '#e08a7a', marginTop: 8, fontSize: 12 } }, '✗ ' + err) : null,
          data ? h('div', { style: { marginTop: 12, display: 'flex', gap: 14, alignItems: 'center' } },
            data.profile.avatar ? h('img', { src: data.profile.avatar, alt: '',
              style: { width: 52, height: 52, borderRadius: '50%', border: '2px solid #6b4a20' } }) : null,
            h('div', null,
              h('div', { style: { fontWeight: 600 } }, data.profile.name || '（无名）'),
              h('div', { style: hint }, '段位 ' + rankLabel(data.profile.rankTier) +
                ' · 好友码 ' + data.accountId +
                (data.winloss ? (' · 近' + data.days + '天 ' + data.winloss.win + '胜' + data.winloss.lose + '负') : '')),
              h('div', { style: hint }, '取数时间 ' + (data.fetchedAt || '').replace('T', ' ').slice(0, 16) +
                (data.cached ? '（缓存）' : ''))
            )
          ) : null,
          data && data.topHeroes && data.topHeroes.length ? h('div', { style: { marginTop: 10 } },
            h('div', { style: { ...hint, marginBottom: 6 } }, '最常玩的英雄（点一下让他上场）'),
            h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 8 } },
              data.topHeroes.slice(0, 8).map((x) => {
                const slug = HERO_SLUG[x.heroId];
                const slugHero = slug && bundled[slug];
                return h('button', {
                  key: x.heroId,
                  title: slugHero ? '本插件内置了这个英雄的动作帧' : '暂无内置动作帧，可选但要等后续版本',
                  style: { ...btn, padding: 4, display: 'flex', flexDirection: 'column', alignItems: 'center', width: 92,
                           opacity: slugHero ? 1 : 0.55 },
                  onClick: () => { if (slugHero) setPref({ hero: slug, action: '' }); }
                },
                  heroPortrait(x.heroId) ? h('img', { src: heroPortrait(x.heroId), alt: '',
                    style: { width: 82, height: 46, objectFit: 'cover', borderRadius: 5 } }) : null,
                  h('span', { style: { fontSize: 11, marginTop: 3 } }, x.games + ' 局 · ' + x.winrate + '%'),
                  h('span', { style: { fontSize: 10, opacity: 0.6 } }, slugHero ? '内置可用' : '暂无帧')
                );
              }))
          ) : null
        ),

        /* --- 本机游戏 --- */
        h('div', { style: box },
          h('div', { style: { fontWeight: 600, marginBottom: 8 } }, '本机 Dota 2'),
          localBlock,
          h('div', { style: { display: 'flex', gap: 8, marginTop: 10 } },
            h('input', { style: input, value: manual, placeholder: '手动指定：D:\\SteamLibrary\\steamapps\\common\\dota 2 beta\\game\\dota',
                         onChange: (e) => setManual(e.target.value) }),
            h('button', { style: btn, onClick: () => refreshState(manual) }, '按这个路径检测'),
            h('button', { style: btn, onClick: () => refreshState() }, '重新自动检测')
          )
        ),

        /* --- 桌宠本体 --- */
        h('div', { style: box },
          h('div', { style: { fontWeight: 600, marginBottom: 8 } }, '桌宠'),
          h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
            h('button', { style: btn, onClick: () => setPref({ enabled: !p.enabled }) },
              p.enabled ? '隐藏桌宠' : '显示桌宠'),
            h('button', { style: btn, onClick: () => setPref({ scale: Math.max(0.5, Math.round((p.scale - 0.2) * 10) / 10) }) }, '缩小'),
            h('button', { style: btn, onClick: () => setPref({ scale: Math.min(2.5, Math.round((p.scale + 0.2) * 10) / 10) }) }, '放大'),
            h('span', { style: hint }, '缩放 ' + p.scale.toFixed(1) + '×')
          ),
          h('div', { style: { ...hint, marginTop: 8 } },
            '内置英雄：' + (heroes.length
              ? heroes.map((x) => x.hero + '(' + x.actions + ' 个动作/' + (x.frameCount || 0) + ' 帧)').join('、')
              : '（正在读取…）')),
          h('div', { style: { display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' } },
            ...heroes.map((x) => h('button', {
              key: x.hero, style: { ...btn, borderColor: p.hero === x.hero ? '#f0cd82' : '#6b4a20' },
              onClick: () => setPref({ hero: x.hero, action: '' })
            }, x.hero))
          ),
          h('div', { style: { marginTop: 12, borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: 10 } },
            h('div', { style: { fontWeight: 600, marginBottom: 6 } }, '贴到桌面（Windows 增强）'),
            h('div', { style: hint }, '另起一个置顶、无边框、真透明的窗口，桌宠就能浮在桌面上，DSH 最小化也还在。'),
            h('div', { style: { display: 'flex', gap: 8, marginTop: 8, alignItems: 'center' } },
              h('button', { style: btn, onClick: async () => {
                const r = await windowStart({ hero: p.hero, scale: p.scale, action: '' });
                setWin(r);
              } }, '打开桌面窗口'),
              h('button', { style: btn, onClick: async () => { setWin(await windowStop()); } }, '关闭'),
              win ? h('span', { style: hint },
                win.ok ? ('✓ 已启动' + (win.pid ? ' pid=' + win.pid : '') + (win.already ? '（已在运行）' : ''))
                       : ('✗ ' + (win.reason || win.error || '失败'))) : null
            )
          )
        ),

        h('div', { style: hint },
          '提示：左键拖动、单击说话、右键菜单、滚轮缩放、双击收起。数据来源 OpenDota（段位/胜率/常用英雄）；' +
          '本机素材（载入画面、头像）会随检测结果逐步接入。')
      );
    }

    /* ==================================================================== */

    return {
      inject: ['slots'],
      apply(ctx) {
        const disposers = [];
        const errors = [];
        function reg(slot, options, Comp, label) {
          disposers.push(ctx.slots.inject(slot, function () {
            const tries = [
              Object.assign({ name: slot }, options),
              Object.assign({ name: slot, id: 'dota2-pet-' + label }, options)
            ];
            for (let i = 0; i < tries.length; i++) {
              try { return ctx.slots.register(tries[i], Comp); }
              catch (e) { errors.push(label + ': ' + (e && e.message ? e.message : String(e))); }
            }
            return function () {};
          }));
        }
        reg('shell.overlay', { id: 'dota2-pet-overlay', order: 200 }, PetOverlay, 'overlay');
        reg('settings.section', { id: 'dota2-pet', order: 61, label: 'DOTA2 桌宠' }, SettingsSection, 'settings');

        return () => { for (const d of disposers) { try { d(); } catch { /* 已移除 */ } } };
      }
    };
  }
});
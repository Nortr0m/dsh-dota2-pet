# 内置动作帧是怎么来的（可复现）

插件里 `assets/frames/<hero>/` 的 257 张 PNG 不是网上找的素材，而是**在你本机的 Dota 2 游戏文件上离线渲染**出来的。这个目录就是那套烘焙工具链，任何人都能按下面的步骤重现同样的结果。

依赖：
- 本机装有 Dota 2（Steam 版或非 Steam 版均可）
- Python 3 + `numpy` + `Pillow`（其它一律不需要）
- [ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat) 的 `Source2Viewer-CLI`（把上游 release 的可执行文件放到 `tools/bake/tools/`，下面用 `$VRF` 指代）

约定：`$GAME` 是游戏目录（含 `pak01_dir.vpk`），例如
`D:\SteamLibrary\steamapps\common\dota 2 beta\game\dota`。

## 1. 抽取游戏数据（只读，不改游戏）

```powershell
$VRF = "tools\bake\tools\Source2Viewer-CLI.exe"
$GAME = "D:\SteamLibrary\steamapps\common\dota 2 beta\game\dota"
$DATA = "tools\bake\data"
New-Item -ItemType Directory -Force $DATA | Out-Null

# 饰品数据库（26661 条饰品 / 1922 套 / 156 个英雄）——约 50 MB
& $VRF -i "$GAME\pak01_dir.vpk" --vpk_filepath "scripts/items/items_game.txt" `
       -o "$DATA\items_game.txt" --game "$GAME\gameinfo.gi"

# 动作库：模型里的 ASEQ 块（官方活动名 ACT_DOTA_* + 循环标记）
& $VRF -i "$GAME\pak01_dir.vpk" --vpk_filepath "models/heroes/slark/slark.vmdl_c" `
       -b ASEQ --game "$GAME\gameinfo.gi" | Out-File "$DATA\slark_ASEQ.txt" -Encoding UTF8

# 英雄定义（技能 Ability1..6 与 12 个饰品槽位 ItemSlots）
& $VRF -i "$GAME\pak01_dir.vpk" --vpk_filepath "scripts/npc/heroes/npc_dota_hero_slark.txt" `
       -o "$DATA\npc_dota_hero_slark.txt" --game "$GAME\gameinfo.gi"
```

## 2. 导出模型（GLB）

```powershell
$EXPORT = "tools\bake\export"
New-Item -ItemType Directory -Force $EXPORT | Out-Null

# 本体（含蒙皮骨骼与 159 条动画）
& $VRF -i "$GAME\pak01_dir.vpk" --vpk_filepath "models/heroes/slark/slark.vmdl_c" `
       -o "$EXPORT\slark.glb" -d --gltf_export_format glb --gltf_export_animations `
       --gltf_export_materials --game "$GAME\gameinfo.gi" --threads 4

# 默认部件（兜帽/披风/护腕/肩甲/武器）：本体只有身体，装备是独立模型
foreach ($part in @('hood','cape','bracer','shoulder','weapon')) {
  & $VRF -i "$GAME\pak01_dir.vpk" --vpk_filepath "models/heroes/slark/$part.vmdl_c" `
         -o "$EXPORT\slark_$part.glb" -d --gltf_export_format glb `
         --gltf_export_materials --game "$GAME\gameinfo.gi" --threads 4
}
```

## 3. 烘焙动作帧

```powershell
python tools\bake\actions.py --hero slark `
       --glb tools\bake\export\slark.glb `
       --aseq tools\bake\data\slark_ASEQ.txt `
       --hero-file tools\bake\data\npc_dota_hero_slark.txt `
       --bake-dir assets\frames\slark --bake-yaws 2 --bake-size 220
```

产出：`<动作>_y<朝向>_f<帧号>.png` + `manifest.json`（每个动作的帧数、时长、帧率、是否循环）。
帧率由游戏内动作时长反推，所以桌宠播出来的节奏和游戏里一致。

## 其它工具

| 脚本 | 用途 |
|---|---|
| `itemlib.py` | 经典 KV + KV3-text 双解析器；`items_game.txt`、`ASEQ/AGRP/DATA` 块都靠它 |
| `actions.py` | 动作库：ASEQ 活动名 → 分类/时长/循环；技能↔动作映射；烘焙与动图 |
| `wardrobe.py` | 衣橱：按套装/槽位从 VPK 取模型 → 并进本体 → 渲染（饰品自由组合） |
| `render.py` | 渲染器：GLB 解析 → 动画采样 → 骨骼世界矩阵 → LBS 蒙皮 → 透视投影 → z-buffer 光栅化 → Gouraud 着色 |

例：看一眼斯拉克的套装与饰品槽位

```powershell
python tools\bake\itemlib.py --hero slark --sample 4
python tools\bake\wardrobe.py --hero slark --list-sets
python tools\bake\actions.py --abilities --list
```

> 说明：`data/`、`export/`、`tools/` 都在 `.gitignore` 里——它们是本机游戏文件与上游可执行文件，
> 既不随仓库分发，也不随 npm 包分发。Dota 2 及其素材版权归 Valve 所有，本工具链只做本机读取与渲染。
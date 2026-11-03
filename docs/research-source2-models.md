# 调研：社区是怎么读取和使用 Dota 2 模型/饰品的

> 调研时间 2026-11-02。结论按"能直接用在 dsh-dota2-pet 上"的标准筛选，出处都附链接。

## 1. 读取游戏文件：Source 2 Viewer / ValveResourceFormat（事实标准）

[ValveResourceFormat](https://github.com/ValveResourceFormat/ValveResourceFormat)（★2550，MIT）是目前唯一成熟的
Source 2 资源解析/导出工具，提供 GUI、CLI 与 NuGet 库（`ValveResourceFormat` + `ValveResourceFormat.Renderer`）。
它没有 Valve 官方文档或代码，全部来自社区逆向。

我们用到的 CLI 能力：

| 用法 | 作用 |
|---|---|
| `--vpk_filepath "models/heroes/x/x.vmdl_c" -o out.glb -d --gltf_export_format glb --gltf_export_materials` | 导出模型 + 材质 + 贴图 |
| `--gltf_export_animations --gltf_animation_list "a,b,c"` | 只导出需要的动作（体积可降一个数量级） |
| `-b ASEQ / AGRP / DATA` | 直接打印动画序列、动画组与模型数据块 |
| `--vpk_filepath "scripts/items/items_game.txt"` | 抽出饰品数据库（26661 条 / 1922 套 / 156 英雄） |

**注意**：VRF 的 README 明确请求使用方在项目里标注来源。本项目已在 README 里标注
"Powered by Source 2 Viewer (ValveResourceFormat)"。

## 2. 别人怎么"用"：三条主流路线

| 项目 | 路线 | 对我们的参考价值 |
|---|---|---|
| [timkurvers/dota2-model-viewer](https://github.com/timkurvers/dota2-model-viewer)（★30，Node 22 + WebGL） | 本地 pipeline server 按需把 VPK 资源转成 glTF/PNG/JSON，浏览器端渲染；支持官方式胸像渲染与批量截图 | **最接近我们的形态**（Node 宿主 + 浏览器渲染）。它的「Known issues」直接印证了我们的坑，见下节 |
| [REDxEYE/SourceIO](https://github.com/REDxEYE/SourceIO)（★984，Blender 4.5+ 插件） | 在 Blender 里导入 VMDL/VTEX/VMAT，做编辑、重打光、导出到别的引擎 | 社区"重度使用"的路线；骨骼/材质/网格组由 Blender 承担，不需要自己写渲染器 |
| [Source2VRChat](https://github.com/luna-gelmini/Source2VRChat) 等移植工具 | 把 Source 模型转成 VRChat 可用 avatar | 处理骨骼与材质兼容性的经验 |

另外：[Chent-AU/vmdl-collision-exporter](https://github.com/Chent-AU/vmdl-collision-exporter)、
[izzetyarali-arch/Source2PortTools](https://github.com/izzetyarali-arch/Source2PortTools) 是更专用的分支。

### dota2-model-viewer 的已知问题（对我们几乎是逐条命中）

摘自其 README：

- **"Heroes and other units lack attachments, such as weapons, armor, and sometimes even their heads."**
  → VRF 的 glTF 导出**不会**给你挂点/饰品，头部有时也是独立模型。必须自己组装（我们就是这么做的）。
- **"Models are not correctly shaded nor lit ... specular, rim and directional ambient lighting are missing."**
  → 自研渲染器普遍缺高光/边缘光/环境光，观感与游戏有差距。
- **"The portrait animation gets chosen based on a pattern matching heuristic, as opposed to retrieving
  the right one directly from the Dota 2 game files."**
  → 这一点我们做得更好：直接从模型 ASEQ 块读官方活动名（`ACT_DOTA_IDLE` / `ACT_DOTA_CAST_ABILITY_1` …）。

## 3. 两个必须知道的具体坑

### 3.1 `@` 前缀的动画是废弃动画（VRF issue #1057）

[Issue #1057](https://github.com/ValveResourceFormat/ValveResourceFormat/issues/1057)：
模型内部常有 `run`（当前使用）/ `@run` / `@@run`（**已废弃**）三份同名变体，
导出时 `@` 前缀可能导致**正确的那个被废弃版本覆盖**。

→ 我们的做法：烘焙时优先选不带 `@` 的名字（`run_anim` / `run` / `shadow_dance`），
把它写进动作挑选规则，避免拿到废弃轨道。

### 3.2 材质是 alpha 裁剪（`alphaMode = MASK`）

实测斯拉克各部件材质均为 `alphaMode: MASK`，贴图里有 **2%–7% 全透明像素**（披风 6.9%、护腕 5.9%、兜帽 2.0%）。
不做 alpha 裁剪，布料镂空处会渲染成实心块——表现就是"穿模/贴图不对"。
glTF 材质里还带 `alphaCutoff`，应当用它而不是写死阈值。

## 4. 我们当前流水线与社区的差异（体检结果）

| 能力 | 社区做法 | 我们 | 结论 |
|---|---|---|---|
| VPK 解析 / 模型导出 | VRF CLI | 同 | ✅ 一致 |
| 饰品/槽位数据 | 读 `items_game.txt` | 同（自写 KV + KV3 解析器） | ✅ 一致 |
| 挂点 | dota2-model-viewer 直接放弃 | 用本体最近邻权重传递 + 兜帽/披风/武器合成 | ✅ 比它更进一步 |
| 动作选取 | 启发式匹配 | 读 ASEQ 官方活动名 | ✅ 更准确 |
| 动画渲染 | 交给 WebGL/Blender | 纯 numpy 光栅化（1–3 fps） | ⚠️ 只适合离线烘焙 |
| **法线贴图** | 引擎/Blender 都支持 | ~~完全没用到~~ → 已接入（TBN + `KHR` 切线空间法线） | ✅ 已补齐（v0.2.1） |
| **alpha 裁剪阈值** | 用材质的 `alphaCutoff` | ~~写死 0.35~~ → 改用材质自带值（实测 0.5） | ✅ 已修正（v0.2.1） |
| **高光 / 边缘光** | 引擎支持 | ~~没有~~ → Blinn-Phong 高光（用材质的 specularTexture 当遮罩）+ 边缘光 | ✅ 已补齐（v0.2.2） |
| 反射/环境光（IBL） | 引擎用 cubemap 反射 | 没有 | ⚠️ 后续可做，需要读 vmat 的 cube map |

## 5. 中文社区

本次会话的 `web_search` 没有可用 key（`DEEPSEEK_API_KEY` 未配置），
知乎/CSDN 这类站点对无头访问普遍有风控（实测 developer.valvesoftware.com 已被 Anubis 挡下），
因此中文资料这一路**没有拿到可引用的来源**。已改走 GitHub API + 原始文档直取。
如需中文社区路线，建议用能正常联网搜索的环境补一轮。
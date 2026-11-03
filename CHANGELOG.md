# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [0.2.2] - 2026-11-03

### 新增
- **渲染补上高光与边缘光**：社区公认自研渲染器与游戏观感差距的主因就是这两项
  （dota2-model-viewer 的 Known issues 明确写了 specular / rim 缺失）。
  高光用 Blinn-Phong 半角向量，强度由材质自带的 `KHR_materials_specular.specularTexture` 当遮罩；
  边缘光按 `(1-|N·V|)^3` 勾勒轮廓。实测：平均亮度 71.7→78.3、高光像素占比 7.2%→9.85%、
  34.3% 像素受影响；每帧渲染 1.10s→1.36s（仅影响离线烘焙）

## [0.2.1] - 2026-11-03

### 新增
- **桌面窗口汉化**：菜单项（待机/移动/攻击/施法/技能/死亡/胜利/放大/缩小/退出桌宠）改为中文。
  做法是宿主半把标签写成 UTF-8 JSON，脚本只读数据——`pet-window.ps1` 必须保持纯 ASCII，
  因为 Windows PowerShell 5.1 会把无 BOM 的 `.ps1` 按 ANSI 解码，源码里写中文会直接读崩语法
- **127 个英雄的官方简体中文名**：客户端从 dota2.com 简体中文 datafeed 生成完整映射并补上
  `heroSlug()`；此前只有 11 个手写中文名，其余显示成"英雄 #id"、英雄按钮显示英文 slug
- **渲染器支持法线贴图**：GLB 一直带 `normalTexture` 与 `TANGENT`，此前完全没用（画面发平）；
  现在按插值后的切线建 TBN，把切线空间法线转到世界空间再打光
- 文档：新增 [`docs/research-source2-models.md`](./docs/research-source2-models.md)（社区做法调研）；
  README 增加 Source 2 Viewer 署名（其 README 明确要求使用方标注）

### 修复
- **烘焙优先选非 `@` 前缀动画**：`@run` / `@@run` 在模型里是**已废弃**动画，VRF 出现过
  "正确的 run 被 @run 覆盖"（[VRF issue #1057](https://github.com/ValveResourceFormat/ValveResourceFormat/issues/1057)）。
  斯拉克内置动作集由 `@run` / `@shadow_dance` 换成 `run` / `shadow_dance`，三个英雄已全部重新烘焙
- **alpha 裁剪改用材质自带的 `alphaCutoff`**（实测各材质均为 0.5），不再写死 0.35
- **版本号统一从 `package.json` 读**：此前宿主半写死 `0.1.0` 而包已是 `0.2.0`，出现漂移
- **修掉重烘后的缓存陷阱**：帧文件名不变但路由给了 `max-age=604800`，重新烘焙后浏览器会用
  一周的旧图。现在烘焙写 `manifest.build` 戳、manifest 改为 `no-cache`、前端用 `?v=<build>` 请求帧

## [未发布]

### 计划中
- 本机素材增强：从本机 Dota 2 的 VPK 直读英雄头像与载入画面（不复制、不落盘）
- 按账号「最常玩英雄」自动选择出场角色；继续扩充内置英雄
- 桌面窗口：多显示器定位、开机自启、点击穿透
- 渲染：补高光与边缘光（社区普遍反映自研渲染器缺这两项，是与游戏观感差距的主因）

## [0.2.0] - 2026-11-02

### 新增
- **内置英雄扩到 3 个**：斯拉克（256 帧）、司夜刺客（266 帧）、食人魔魔法师（264 帧），合计 786 帧
- **烘焙工具链按英雄自己的动作库自动挑动作**（`pick_bake_set`）：待机/移动/攻击/技能×2/死亡/胜利，
  同名变体（`_injured`、`@@*_latch`、`*_anim`）归一化去重，短动作优先，排除 `versus_attack` 这类 40 秒级特例
- **manifest 写入动作分类**（中文 + ASCII 双份）：前端右键菜单与 PowerShell 桌面窗口菜单都按当前英雄
  实际拥有的动作动态生成，不再写死斯拉克的动作名
- **支持「模型目录 ≠ 英雄 slug」**：司夜刺客 slug 是 `nyx_assassin`，模型却在 `models/heroes/nerubian_assassin/`，
  默认部件解析改为显式传模型目录（不传时退回 slug，客观上找不到会返回空）
- **测试覆盖所有内置英雄**：新增 `test/frames.test.mjs`，自动遍历 `assets/frames/` 下每个英雄校验
  manifest 结构、帧数与文件一一对应、PNG 魔数；CI 与 Release 里的重复内联脚本随之删掉

### 修复
- **部件刚性绑定挂错骨骼**（`render.py` 的 `merge_parts`）：最近骨骼存成了**节点 id**，而 `JOINTS_0`
  要的是骨骼在 `skin.joints` 中的**槽位序号**。斯拉克的节点 id 恰好都小于关节数，所以既不抛异常、
  画面也"看着像"，其实装备全挂错了骨骼；食人魔节点更多，直接 `IndexError: index 50 is out of bounds`。
  修好后三个英雄的 `JOINTS` 最大值分别为 47/67/49，均小于各自关节数 48/68/50，并已全部重新烘焙

## [0.1.0] - 2026-11-01

首个可用版本。

### 新增
- **DSH 插件形态**：`dsh.bundle.patch` + `dsh.client`（web），宿主半 + 前端半双半结构
- **本机 Dota 2 检索**（`lib/find-dota.mjs`）：手填路径 → Windows 注册表（3 处）→ 常见安装位置 × 全盘符 → `steamapps\libraryfolders.vdf` 与 `config\libraryfolders.vdf` → 全盘兜底扫 `steamapps\common\dota 2 beta`；判定标准为 `pak01_dir.vpk` 可打开，并用 `appmanifest_570.acf` 交叉验证
- **账号配置**：填好友码/账号 ID 或 Steam64 即可，无需 API key；宿主半代理 OpenDota（含 CORS 规避与 10 分钟缓存）
- **浮层桌宠**（`shell.overlay`）：动作帧播放、拖动、单击说台词、右键菜单、滚轮缩放、双击收起，位置与缩放持久化
- **设置页**（`settings.section`）：账号、本机游戏检测（含手填与重测）、桌宠开关/缩放/换英雄、桌面窗口开关
- **桌面窗口（Windows 增强）**：`lib/pet-window.ps1` 用 WPF 创建无边框、真透明、置顶、不进任务栏的窗口，DSH 最小化后仍留在桌面
- **内置动作帧**：斯拉克 8 个动作 × 2 个朝向共 256 帧（约 5.9 MB），由 `tools/bake` 从本机游戏模型离线烘焙，帧率按游戏内动作时长反推
- **可复现烘焙工具链**（`tools/bake/`）：KV/KV3 解析、饰品套装组合、动作库读取（ASEQ 活动名）

### 修复
- Steam64 ⇄ 账号 ID 换算改用 `BigInt`：`76561197960265728` 超过 JS 安全整数范围，用 `Number` 会丢精度（实测偏差 8，会查到别人的账号）
- Windows PowerShell 5.1 读取 `.ps1` 的编码问题：脚本改为纯 ASCII 内容并带 UTF-8 BOM，否则中文字符串会被按 GBK 解码导致语法错误
- 路由注册规避宿主 `prefix` 只对完全一致路径生效的限制：带查询串的接口注册裸路径，静态帧按文件逐条注册精确路由

[未发布]: https://github.com/Nortr0m/dsh-dota2-pet/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/Nortr0m/dsh-dota2-pet/releases/tag/v0.2.2
[0.2.1]: https://github.com/Nortr0m/dsh-dota2-pet/releases/tag/v0.2.1
[0.2.0]: https://github.com/Nortr0m/dsh-dota2-pet/releases/tag/v0.2.0
[0.1.0]: https://github.com/Nortr0m/dsh-dota2-pet/releases/tag/v0.1.0
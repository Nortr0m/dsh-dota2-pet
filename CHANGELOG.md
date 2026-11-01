# 变更日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 计划中
- 本机素材增强：从本机 Dota 2 的 VPK 直读英雄头像与载入画面（不复制、不落盘）
- 按账号「最常玩英雄」自动选择出场角色；继续扩充内置英雄
- 桌面窗口：多显示器定位、开机自启、点击穿透

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

[未发布]: https://github.com/Nortrom/dsh-dota2-pet/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Nortrom/dsh-dota2-pet/releases/tag/v0.2.0
[0.1.0]: https://github.com/Nortrom/dsh-dota2-pet/releases/tag/v0.1.0
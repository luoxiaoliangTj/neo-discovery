# NEO Catalog Discovery System

近地天体（NEO）候选体发现系统：从 NASA NEOWS 公开数据中自动筛查"可能的新发现"。

## 架构

```
NEOWS Browse API ──→ SQLite 星表 (~52k NEO)
                          ↓
NEOWS Feed API ────→ 交叉比对 → 候选体验证 → 威胁评分 → HTML报告 → 飞书推送
                          ↓
                    NASA 详情 API ──→ 轨道质量验证 (U参数 + 数据弧长 + 轨道合理性)
```

## 核心功能

- **星表全量下载**: NEOWS browse API 并发拉取 ~62k NEO，支持断点续传 + 限流退避
- **交叉比对**: NEOWS feed（未来7天接近事件）vs 本地星表 → 发现"新出现"天体
- **候选体验证**: 四级过滤（轨道合理性 → 数据质量 → 编号变更检测 → 置信度分级）
- **威胁评分**: PHA + 距离 + 速度 + 直径多因子评分（0-10）
- **飞书推送**: 确认发现自动推送，待观察/误报只记录不推送

## 快速开始

```bash
# 安装依赖（仅 requests）
pip install requests

# 运行
python3 neo_catalog_system.py
```

## 配置

环境变量：
- `NASA_API_KEY` — NASA API key（默认使用 DEMO_KEY，有每小时 2000 次限流）

可选修改 `CONFIG` 字典：
- `check_days` — feed 扫描天数（默认 7）
- `rate_limit_pause` — 请求间隔（默认 2s，安全不超 2000次/小时）
- `max_workers` — 并发数（默认 1，调高可能触发限流）

## 输出

- `neo_catalog.db` — SQLite 数据库（星表 + 发现记录 + 扫描日志）
- `output/neo_discovery_report.html` — HTML 报告
- `output/neo_discovery_report.json` — JSON 摘要

## 与专业系统的定位差异

| 能力 | 本系统 | NASA Sentry / ESA NEODyS |
|------|--------|--------------------------|
| 目标 | 候选体筛查（快速确认 feed 中的新天体） | 撞击风险评估（精密轨道计算） |
| 轨道 | 使用预计算根数 | 从观测数据反推轨道 |
| 撞击概率 | ❌ 仅威胁评分 | ✅ Palermo/Torino 尺度 |
| 公布 | 仅内部筛查，不直接公布 | 官方国际警报 |

本系统定位为**筛查工具**：比 MPC 早 1-3 天发现"可能是新的"，不替代官方确认流程。

## License

MIT

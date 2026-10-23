# 仓库协作指南

## 项目与文档

这是企业微信客户流失监控试用项目：Python 3.12、FastAPI、SQLAlchemy 异步 SQLite、Alembic，前端为 Vue 3、TypeScript、ECharts，支持钉钉内部应用登录。

- 启动及依赖管理以 `README.md` 为入口；当前行为参考 `docs/实现说明.md`、`docs/后端接口说明.md`。
- 客户事件规则见 `docs/群成员识别状态.md`；平台配置与协议映射见 `docs/企业微信与钉钉接口及配置说明.md`。
- `docs/requirement/` 和 `docs/第1天技术方案.md` 是需求及设计背景，不等同于已交付功能。
- 用户界面、项目说明和交付记录沿用中文。功能变更同步更新相关文档，报告实际执行的验证及未验证项。

## 代码位置

- `app/main.py`：应用工厂、生命周期和静态前端服务。
- `app/api.py`：回调、登录、主体授权、事件查询及管理接口。
- `app/worker.py`：持久化任务、重试、资料快照和事件处理。
- `app/event_classification.py`：群成员证据与业务分类。
- `app/platforms.py`、`app/security.py`、`app/media.py`：平台适配、签名加解密、受限来源的媒体下载。
- `app/models.py`、`app/db.py`、`app/config.py`、`app/cli.py`：数据模型、数据库、配置及运维命令。
- `frontend/src/`：界面、API 客户端、群事件识别依据和样式；修改源码后重新构建。
- `migrations/`：Alembic 迁移及冻结的历史模型/分类逻辑。
- `tests/`：后端测试和浏览器冒烟；`scripts/`：平台诊断及群事件前端检查。
- `deploy/`：部署、同步更新及反向代理配置；`artifacts/` 是交付产物，不是日常源码修改入口。

## 环境与常用命令

以下命令默认在仓库根目录的 PowerShell 中执行。Python 统一使用 uv；提交依赖变更时同时维护 `pyproject.toml` 和 `uv.lock`，不要另建 requirements 文件。前端使用 `frontend/pnpm-lock.yaml`。

```powershell
uv sync --locked
uv run python -m app.cli init-env --demo
uv run python -m app.cli init-db
uv run python -m app.cli seed-demo
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend run build
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

初始化和填充演示数据仅用于本地演示环境；不要向真实数据环境灌入演示数据。`init-env` 不覆盖已有 `.env`。Uvicorn 直接启动不会自动迁移数据库，升级后先运行 `init-db`。当前架构使用单 worker。

前端开发运行 `pnpm --dir frontend dev`，将 `PUBLIC_ORIGIN` 设为 `http://localhost:5173`；Vite 将 `/api` 代理至后端。构建后同源访问时改回实际后端访问地址，修改环境配置后重启后端。

## 验证

按变更范围选择检查；后端行为变更补充有意义的回归测试，前端变更执行类型检查和构建。

```powershell
uv run pytest -q
uv run ruff check app tests migrations deploy scripts
pnpm --dir frontend run build
node scripts/test-group-evidence.cjs
```

- `build` 包含 `vue-tsc --noEmit`。群事件前端检查依赖已安装的 `frontend/node_modules/typescript`。
- 根目录 `package.json` 的 `test` 是占位命令，不作为测试入口。
- 浏览器冒烟：`node tests/browser-smoke.cjs`。先准备本地演示数据库、构建前端并启动 `http://localhost:8000/`；需要 Playwright 和 Chromium，可用 `BROWSER_CHANNEL=msedge` 选择本机 Edge，`PLAYWRIGHT_MODULE` 指定已有模块位置。
- 测试使用临时数据库、独立配置和模拟平台响应，沿用现有 pytest/httpx 测试方式，避免依赖真实凭据或修改现有业务库。
- 纯文档修改检查内容与命令是否符合仓库现状，无需为此启动服务或执行在线平台诊断。

## 必须保留的业务约束

- 区分员工删除客户（`del_external_contact`）和客户删除员工（`del_follow_user`），保留删除方向。
- 业务分类与识别依据分开：符合快照匹配规则的外部客户可以计入 `group_left` / `group_removed`，同时保留 `resolution=inferred`；不能声称平台逐人确认。
- 身份、成员类型或退出原因不足时保留 `group_change_unknown` / 待核实。内部员工不计入外部客户流失；不编造成员或操作人，不把群主当作操作人。
- 混合成员回调拆分后保持总人数守恒、稳定的事件键和重试幂等；列表、卡片、趋势和排行使用一致的业务分类，并保持主体权限隔离。
- 修改事件规则时一并核对 worker、分类函数、查询统计、前端识别说明和迁移测试。

## 数据、安全与迁移

- 回调验签解密后，收件箱和任务在同一事务中可靠保存，提交成功后才响应平台；保留去重和失败重试能力。
- 网络请求不要放进数据库事务；保持 SQLite WAL、外键检查及现有持久化设置。
- 数据结构或历史数据规则变更新增 Alembic 迁移，保持既有迁移及冻结辅助代码可重放，不让历史迁移依赖变化中的应用模型。数据迁移验证升级、降级及幂等性。
- 保持会话、CSRF、主体授权和头像下载来源限制。生产配置要求 HTTPS、Secure Cookie、关闭演示模式。
- 不提交或输出 `.env`、Secret、Token、AESKey、一次性登录 code、真实个人资料或本地部署凭据。示例只用占位值；诊断输出脱敏。
- 在线诊断会访问真实平台，先读脚本参数和相关说明；获取 token 成功不代表业务 API 权限、真实回调或钉钉免登成功。
- 本地模拟测试、浏览器冒烟、客户端回调挑战和真实平台端到端验证分别报告；没有本次证据时，不宣称已部署或已通过真实联调。

## 工作区习惯

- 开始前查看 `git status`，保留用户已有修改，避免无关重构和生成产物改动。
- 不把 `.venv/`、`node_modules/`、`frontend/dist/`、数据库、媒体、备份和缓存当作源码；只有任务明确涉及交付包时才更新相关产物。
- PowerShell 多行诊断优先使用独立脚本或 here-string，避免复杂嵌套引号；搜索时排除依赖、缓存和数据目录。

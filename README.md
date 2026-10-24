# 企微用户流失监控

Python + FastAPI + SQLite 后端，Vue 3 + TypeScript + ECharts 前端。支持本地演示和企业内部应用接入。

已实现回调验签解密、可靠入库与去重、可恢复任务处理、客户资料与头像补全、看板、明细、钉钉登录适配、主体授权、管理页面和数据库备份。

新增只读客户分析助手：电脑侧边面板、手机全屏对话，提供 21 类数据能力，覆盖主体、员工、客户、群与成员、跟进关系、业务事件、统计趋势/排名、头像、当前账号，以及管理员可见的主体配置、任务、回调、用户授权和审计。支持筛选分页、继续查看相关记录、周期对比、事件解释和报告草稿；可复用 vLLM 模型。接入和验证见 [AI 助手使用与接入](docs/AI助手使用与接入.md)。

**已有真实部署及部分企微、钉钉验收结果，完整验收尚未闭合。** 2026-10-23 的记录包含真实工作台免登、主动退群/被移出与历史头像、小米14交互及3440×1440钉钉PC布局通过反馈；生产安全配置、演示数据隔离、员工头像授权、操作人来源和部分真实场景仍待收口。结果与范围见 [真实验收记录](docs/真实钉钉环境验收记录.md) 和 [当前未完成项](docs/需求1-8章当前未完成项复查.md)。本轮仅核对源码与既有记录，未重新连接服务器或平台。

群变更按业务动作分类：快照证据足以关联外部客户时，计入主动退群或被移出群，保留 inferred 识别依据；内部员工和证据不足的记录留在“其他 / 待核实”，不编造操作人。判定及历史升级见 [客户事件分类与识别依据](docs/群成员识别状态.md)。演示数据不代表真实平台结果。

文档与代码核对日期：2026-10-24，源码基线 `24e6586`。启动和依赖管理以本文为入口，当前行为见 [实现说明](docs/实现说明.md) 和 [后端接口说明](docs/后端接口说明.md)，验证结果见 [自测报告](docs/自测报告.md)，专项记录见 [文档索引](docs/文档索引.md)。`docs/requirement/` 保留原始需求，[第 1 天技术方案](docs/第1天技术方案.md) 保留评审设计，不作为已交付清单。

## 本机快速启动

源码部署后的日常更新可使用 [本地一键同步部署](docs/本地一键同步部署.md)：本地构建前端，SSH 上传并重启服务器、检查 `/readyz`。

环境：uv、Python 3.12、Node.js 24、pnpm。uv 安装方法见 [官方文档](https://docs.astral.sh/uv/getting-started/installation/)。以下命令在项目根目录的 PowerShell 中执行；uv 会自动创建 `.venv`，无需手动激活。

```powershell
uv sync --locked
uv run python -m app.cli init-env --demo
uv run python -m app.cli init-db
uv run python -m app.cli seed-demo
```

`init-env` 只在 `.env` 不存在时创建，生成随机密钥和演示密码，不覆盖已有配置。`seed-demo` 创建两个主体、管理员与普通用户，以及 60 天模拟数据；重复执行不会重复插入。演示密码见本地 `.env` 的 `DEMO_PASSWORD`，不要提交该文件。

构建前端：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 [本地工作台](http://localhost:8000/)。进入演示时可选管理员或普通用户；普通用户只能访问总部主体。开发接口文档为 [OpenAPI 文档](http://localhost:8000/docs)。

已有环境仅在配置、数据库迁移及前端构建均就绪时，可直接运行最后一条 Uvicorn 命令；直接启动 Uvicorn 不会自动迁移数据库，更新后先执行 `uv run python -m app.cli init-db`。容器入口 `deploy/start.py` 会先迁移再启动。应用必须使用单 worker；本地密码不硬编码在源码或网页中。

如果本机仅有 Node 而没有 pnpm，请按公司认可的方式安装 pnpm。当前工作环境使用随运行时提供的 pnpm 执行构建，标准项目并不依赖其机器绝对路径。

## Python 依赖管理

Python 依赖统一使用 uv 管理：`pyproject.toml` 声明依赖范围，`uv.lock` 固定各平台的解析结果，两个文件一起提交。`.python-version` 将本地解释器固定到 Python 3.12 系列。项目以源码目录运行，无需安装为 Python 包。

```powershell
# 按锁文件安装运行依赖和开发依赖
uv sync --locked
# 添加或删除运行依赖（将 package-name 替换为实际包名）
uv add package-name
uv remove package-name
# 添加开发工具
uv add --dev package-name
# 主动升级所有依赖并同步环境，升级后运行测试
uv lock --upgrade
uv sync --locked
# 生产环境仅安装运行依赖
uv sync --locked --no-dev
```

日常使用 `uv run ...` 运行项目命令，默认包含开发依赖；本机使用生产环境时，运行命令加 `--no-dev`，例如 `uv run --no-dev uvicorn app.main:app --host 127.0.0.1 --port 8000`。不再维护独立的 requirements 文件。容器构建使用 `uv sync --locked --no-dev`，并通过 PATH 直接使用已安装的虚拟环境。

## 开发与目录

```text
app/
  api.py          身份、权限、查询、管理、回调路由
  models.py       SQLite 模型
  security.py     回调签名与 AES 解密、游标签名
  worker.py       持久化收件箱、后台任务、资料与事件处理
  event_classification.py  群成员证据与外部客户业务分类
  platforms.py    企业微信及钉钉企业内部应用接口适配
  media.py        受限来源的头像下载
  cli.py          初始化、管理员、快照同步、备份
frontend/         Vue H5 页面和前端锁文件
migrations/       Alembic 迁移、冻结的历史模型与群事件分类逻辑
tests/            后端集成测试、浏览器冒烟测试
docs/             需求、技术方案、配置指南、实现说明与自测记录
scripts/          平台诊断、回调检查、头像补全和前端识别检查
deploy/           源码/容器部署、同步更新、服务及反向代理配置
```

前端开发从项目根目录运行 `pnpm --dir frontend dev`。开发时将 `.env` 的 `PUBLIC_ORIGIN` 设为 `http://localhost:5173` 并从该地址访问前端，Vite 将 `/api` 代理到后端；构建后同源访问 8000 端口时改回 `http://localhost:8000`。修改环境变量后重启后端。

新增、删除支持按员工姓名或 ID 直接搜索，并可指定员工精确筛选；群变更支持群名搜索。三类明细共用上一页/下一页和每页 10/20/50 条选择，分页栏吸底，翻页固定首屏日期和数据上界；支持下拉刷新。汇总无日期筛选时指标与排名近 7 天、趋势近 30 天，填写任一日期后统一使用补齐的区间。页面适配手机、普通电脑及超宽屏，具体行为见实现说明。

SQLite 使用 WAL、外键检查、5 秒忙等待和 FULL 同步。网络请求不在数据库事务内执行。回调收件箱与任务在同一事务中保存，提交成功后再响应平台。后台消费者空闲时每秒检查任务，单次处理超时 120 秒、租约 180 秒；失败采用指数退避并带随机抖动，第 5 次尝试仍失败时进入失败队列，可在管理页重试。

## 真实应用配置

详见 [配置指南](docs/配置指南.md)。请在独立数据库中接入真实数据，不能把演示库直接作为生产库。

1. 在 `.env` 填入钉钉内部应用参数、企微客户联系凭据和实际 HTTPS 地址。
2. 关闭 `DEMO_ENABLED`，初始化一个新的数据库。
3. 使用 CLI 创建真实管理员，再通过钉钉工作台登录。
4. 在管理页新增企微主体，凭据引用对应 `.env` 中的 `WECOM_<REF>_*`。
5. 将管理页生成的回调 URL、配置的 Token 和 AESKey 填入企微后台，执行 URL 验证。
6. 初始化资料快照，再开展添加、删除、退群和踢人测试。

```powershell
uv run python -m app.cli create-admin --corp-id ding真实企业ID --user-id 真实用户ID --name 管理员姓名
uv run python -m app.cli sync-profiles --tenant-id 1
```

群和客户列表可见范围由公司提供的 API 权限决定。快照同步不是历史事件导入。Secret、回调 Token 和 AESKey 均不通过管理页面读取或返回。

`sync-profiles` 还会保存群成员基准并预取成员头像；建群及成员进群回调使用同一流程。新主体应先完成同步，再开展离群测试；缺少事前快照时不能靠当前群列表还原历史离群身份。

## 测试与备份

检测企业微信配置（默认凭据引用 `ACME`）：

```powershell
uv run python -m scripts.check_wecom
```

脚本读取 `WECOM_ACME_SECRET`、`WECOM_ACME_TOKEN`、`WECOM_ACME_AES_KEY`。企业 CorpID 从 `--corp-id` 或可选配置 `WECOM_ACME_CORP_ID` 读取；未指定时只读查询数据库中凭据引用为 ACME 的唯一启用主体。多个主体共享引用时需显式选择，脚本不会自动猜测。

```powershell
# 指定企业 ID，不是钉钉 CorpID
uv run python -m scripts.check_wecom --ref ACME --corp-id 你的企微CorpID
# 使用管理页面中的主体 ID 及对应凭据引用
uv run python -m scripts.check_wecom --ref ACME --tenant-id 1
# 不联网，仅检查配置格式及本地加解密
uv run python -m scripts.check_wecom --ref ACME --offline
# 可选：对管理页提供的回调 URL 发起 GET 挑战，不创建业务事件
uv run python -m scripts.check_wecom --ref ACME --callback-url "https://你的域名/callbacks/wecom/实际标识"
```

默认在线检查 CorpID 与 Secret 换取 access_token、客户联系员工列表权限、客户群列表权限（最多请求一项）；不修改企业配置、不发送消息、不保存员工或客户群资料。回调 Token 与 AESKey 可进行本地加解密验证，但只有在企微后台完成 URL 保存并接收到真实事件，才算平台端到端通过。客户端 GET 挑战通过也不证明企微服务器可达。

脱敏报告默认保存在 `data/wecom-check.json`。支持 `--env-file` 和 `--report`；退出码 0 表示已执行项目通过，SKIP 项仍未验证，退出码 1 表示有失败项。不要把一次获取 token 成功当作全部业务接口权限或完整群成员明细能力都可用。

检测已经填写的钉钉内部应用配置：

```powershell
uv run python -m scripts.check_dingtalk
```

默认读取项目根目录 `.env`，环境变量优先，检测四个字段并调用官方接口验证 Client ID 与 Client Secret。脚本不会输出或保存凭据、accessToken 和个人资料，脱敏结果保存在 `data/dingtalk-check.json`。没有一次性 code 时，不会声称免登或用户详情权限已通过；CorpID 和 AgentId 的归属仍需与后台核对。

使用其他配置文件：`--env-file D:/你的目录/.env`。需要完整检查 code 换取用户身份及用户详情时，在交互式终端运行：

```powershell
uv run python -m scripts.check_dingtalk --with-login
```

按提示隐藏输入从该钉钉 H5 应用新取得的一次性 code，验证会消费该 code。不要将 code 写进命令行或发到聊天中。退出码 0 表示已执行项目通过，仍需查看 SKIP 项；退出码 1 表示存在失败项。

```powershell
uv run pytest -q
uv run ruff check app tests migrations deploy scripts
node scripts/test-group-evidence.cjs
uv run python -m app.cli backup --output backups/manual-backup
```

前端 `pnpm run build` 在 `frontend/` 中执行，同时执行 TypeScript 类型检查。根目录 `package.json` 的 `test` 是占位命令，后端使用上述 pytest 命令。浏览器冒烟测试从项目根目录运行 `node tests/browser-smoke.cjs`，需要先启动 `http://localhost:8000/` 演示服务、准备演示数据，并安装 Playwright 及可用 Chromium，或设置 `BROWSER_CHANNEL=msedge` 使用本机 Edge。`PLAYWRIGHT_MODULE` 可指定已有 Playwright 模块路径，测试从 `.env` 读取演示密码，不将密码写入输出。

备份命令使用 SQLite 在线备份，并复制媒体目录。备份目录必须不存在。恢复时先停止服务，恢复数据库及媒体，单独恢复原 `PAYLOAD_KEY` 和其他安全配置，运行迁移后启动服务。媒体文件不原地修改；备份期间如有新增图片，允许出现未被数据库引用的额外文件。

只读查看最近回调、任务及事件可运行 `uv run python -m scripts.check_callbacks --limit 10`，不会重放回调或调用平台。历史头像补全先用 `uv run python -m scripts.backfill_avatars` 查看目标计数；加 `--apply` 才会备份数据库、请求平台并写入头像引用，不能恢复没有可靠身份或历史来源的照片。以上工具范围见 [配置指南](docs/配置指南.md)。

## 部署

**宝塔服务器不使用 Docker 时，按 [宝塔非容器部署指南](docs/宝塔非容器部署.md) 操作**：Python 3.12 + uv 安装依赖，使用 `deploy/wecom-monitor.service` 管理服务，环境模板为 `deploy/baota-native.env.example`，宝塔 Nginx 代理到本机 8000 端口。前端可本地构建后上传。

如需容器方案，见 [宝塔 Docker 部署指南](docs/宝塔部署.md)：使用独立的 `compose.baota.yaml`，环境模板为 `deploy/baota.env.example`。两种方案共用 `deploy/baota.nginx.conf` 反向代理片段。

本期按单机部署，数据目录需要持久化。可使用公司现有 Python 服务和 HTTPS 反向代理，也可参考 `Dockerfile`、`compose.yaml` 和 `deploy/Caddyfile`。已有源码同步发布及公网核对记录，见 [带鱼屏版本部署与验证](docs/带鱼屏自适应优化与验证.md)；Docker 构建及容器部署尚无验收通过记录。

默认 `compose.yaml` 由 Caddy 提供 HTTPS，启动前在 `.env` 中额外设置 `APP_DOMAIN=你的实际域名`（仅主机名），并将 `PUBLIC_ORIGIN` 设为同一域名的 HTTPS 地址。宝塔方案单独使用 `compose.baota.yaml`，无需 `APP_DOMAIN`。两份配置均覆盖容器内数据库和媒体路径为 `/app/data` 下的持久化路径。

生产配置要求 `APP_ENV=production`、`COOKIE_SECURE=true`、HTTPS `PUBLIC_ORIGIN` 和 `DEMO_ENABLED=false`。启动时自动拒绝不安全的生产组合；生产环境关闭 OpenAPI 页面。实际并发和容量需在公司硬件、网络及数据规模下验收。

# 本地源码开发

当前采用 Windows 本机源码运行：Python 3.12 后端 + Vite 前端。
在仓库根目录执行：

```powershell
Set-Location 'D:\projects\SELF\zero evo\ArcReel'
.\start-local.ps1
```

访问 http://localhost:5173 。后端地址为 http://localhost:1241。
用户名和密码见仓库根目录 `.env`（从原部署配置复制）。
前端热更新。后端默认直接运行源码，修改 Python 后需在没有生成任务或对话运行时重启；Windows 自动重载可能遗留旧 worker，因此默认关闭。纯开发时可用 `.\start-local.ps1 -Reload` 监视 `server/` 与 `lib/`。
日志位于 `deploy/native-backend*.log` 和 `deploy/native-frontend*.log`。

首次安装或依赖文件变更后：

```powershell
$env:UV_CACHE_DIR = 'D:\ArcReelCache\uv'
$env:UV_HTTP_TIMEOUT = '300'
uv sync --python 3.12 --no-dev
Set-Location frontend
corepack pnpm install --frozen-lockfile --store-dir D:\ArcReelCache\pnpm
corepack pnpm build
```

数据保存在本目录 `projects/`，SQLite 数据库是 `projects/.arcreel.db`；
全局素材文件在 `projects/_global_assets/`。根 `.env` 通过 `ARCREEL_DATA_DIR`
复用原部署的数据目录。

Codex Agent 使用自己的只读沙箱与项目业务工具。保留的 Claude Agent 在 Windows 上按原项目实现降级为命令前缀白名单；完整 Linux 沙箱需 WSL2/Docker。

## 可选的源码镜像构建

`compose.source.yaml` 保留了本地源码镜像构建配置。Docker 恢复后可在本目录运行：

```powershell
docker compose -f docker-compose.yml -f compose.source.yaml up -d --build
```

该模式与本机后端共用 1241 端口，须先停止本机后端，且源码修改后须重新构建。
本次 Docker 构建因 C 盘耗尽中断，尚未完成。Docker 数据盘已完整校验并迁移到
`D:\DockerDesktopData\disk`，原路径通过目录链接指向该位置；Docker Desktop
仍有残留通信文件导致的启动故障，未重置或删除其镜像、容器数据。

## 内置素材入口

- `frontend/src/data/style-templates.ts`：36 个风格模板及缩略图 URL。
- `frontend/public/style-thumbnails/`：36 张随仓库提供的 PNG。
- `frontend/src/i18n/zh/templates.ts`：中文名称、标签。
- `lib/style_templates.py`：生成时使用的风格提示词。
- `server/routers/assets.py`：本机全局资产库 API，读取数据库及本地文件。
- `frontend/src/onboarding/demo-project.ts`：新手引导的前端演示数据。
- `frontend/src/onboarding/demo-placeholder.ts`：引导演示 SVG 占位图。

以上源码路径均相对仓库根目录。

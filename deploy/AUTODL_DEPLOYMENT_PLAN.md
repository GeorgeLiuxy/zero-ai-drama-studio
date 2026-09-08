# ArcReel + AutoDL 最终部署方案与 Codex 执行任务书

版本：1.0  
核查日期：2026-09-08  
适用对象：Windows 本地 ArcReel + AutoDL 单张 RTX PRO 6000 Blackwell 96GB、100～120GB 系统内存。  
目标：先在无卡模式完成可完成的准备，再在有卡模式完成真实推理、联调和生产准入；按批次生产，减少昂贵 GPU 的空等时间。

> 本文是实施方案，不是目标服务器实测报告。附带脚本只进行只读环境审计或小规模 GPU 运算检查，不是“一键安装全部模型”的脚本。不能把下载完成、依赖安装成功或模拟接口通过，标记为模型已经可以生产。

## 1. 最终决策，不再扩散选型

### 1.1 部署边界

- **Windows 本地**：ArcReel 工作台、正式项目数据库、角色/声音/镜头资产、Codex、人工审核、剪辑与最终归档。复用已有部署，不重装能运行的部分。
- **AutoDL**：ComfyUI 视频执行环境、CosyVoice 独立执行环境、轻量任务适配服务。只保存执行状态、模型缓存、输入副本和等待回收的输出。
- **连接**：SSH 隧道；默认不开放公网模型接口。不把 SSH 私钥、平台密码或 API Key 写入仓库、日志和报告。
- **单卡调度**：所有模型共享一个全局 GPU 执行槽，初始并发为 1；不是“视频=1、配音=1、图片=1”各跑一个。
- **生产责任**：ArcReel 是正式项目/镜头/版本的管理入口；远端只保存执行账本，不再建设第二套创作平台。

ArcReel 官方已有媒体后端、持久任务、版本和合成等能力，应优先利用。[S03][S04]

### 1.2 服务器采购/租赁规格

| 项目 | 本次选择 |
|---|---|
| GPU | 单张 NVIDIA RTX PRO 6000 Blackwell，96GB 显存；核对型号全名和实例实际可用显存 |
| CPU | 主机配套资源，8～16 vCPU 级或更高即可；不要为 CPU 型号额外付高价 |
| 正常模式内存 | 100～120GB，价格接近优先 120GB；以容器实际配额为准 |
| 数据盘 | 先约 300GB **总可用容量**，支持后续扩容；不是授权直接扩到更大容量 |
| 操作系统 | 平台官方 Linux/Ubuntu 镜像，优先 22.04 LTS 或已验证的 24.04 镜像 |
| GPU 软件环境 | 支持 Blackwell；视频环境优先 cu130 路线，租前确认宿主驱动条件 |
| 计费 | 按量；先无卡准备，后有卡验证；不默认包月或连续开机 |
| 可用性 | 接受关机释放算力后的再次开机等待，不承诺 24×7 高可用 |

CPU、磁盘、镜头规格和资源余量是本方案预算，不是模型官方统一最低线。100～120GB 不代表任意模型、任何分辨率和长度都能运行。

### 1.3 模型白名单

| 层级 | 固定选择 | 本轮动作 |
|---|---|---|
| 主视频 | Wan2.2 I2V-A14B，ComfyUI 原生 FP8 工作流 | 必做，先固定一个图生视频模板 |
| 默认旁白/角色声音 | FunAudioLLM/Fun-CosyVoice3-0.5B-2512 | 必做，独立 Python 环境，先原生 PyTorch 推理 |
| 原生有声视频增强 | MiniMax H3 FL2VA：ComfyUI 提供的剪枝 INT8 模型与配套量化编码器 | 主线通过后，在同机单独验证；下载/运行前确认许可证和额度 |
| 首帧/角色参考图 | 沿用现有可用图片流程，或导入已确认图片 | 不为了这次视频/旁白部署同时换掉出图路线 |
| 文字创作 | 现有 Codex 与已配置的文本能力 | 不在昂贵 GPU 上再部署大型语言模型 |
| 合成 | 本地 ArcReel/FFmpeg/剪映 | 复用，不新建时间线编辑器 |

**暂不安装**：额外 T2V 大模型、第二套大型有声视频模型、音乐大模型、全套口型模型、训练/LoRA 训练栈、vLLM、TensorRT-LLM、Kubernetes。Wan S2V 或口型模型可在“必须逐字对白且需要正面口型”的真实需求出现后增补。

FP8 是初始可复现配置，不是断言其效果总优于 BF16。已有工作流通过检查可复用；精度变化应另做小范围对比，不在部署阶段反复换模型。[S05][S06][S07]

**旁白是独立能力**：CosyVoice 是本方案选定的自建后端，并非 ArcReel 自动附带的默认权重。ArcReel 目前文档描述的 TTS 接入包括百炼 Qwen 或兼容接口，必须核对实际请求并适配。[S03]

## 2. 无卡模式的实际限制

AutoDL 当前文档说明，无卡模式为 **0.5 核 CPU、2GB 内存、无 GPU，参考价格 0.1 元/小时**；切换不会主动删除原有文件，但 GPU 不保留，之后正常开机可能没有空闲卡。付费扩容磁盘另外计费。[S01][S02]

因此采用：

**本地开发/静态测试 → 无卡下载与轻量安装 → 有卡补齐必要编译/真实推理 → 本地验收与备份。**

### 2.1 无卡允许执行

- 通过 SSH 运行只读审计；查看版本、磁盘、实际内存限制和已有文件。
- 克隆指定代码版本、创建目录、准备配置、下载预编译 wheel。
- 按文件白名单流式下载权重；下载并发默认为 1，单个进程顺序执行。
- 尝试安装预编译依赖；内存不足时只记录阻塞，把相应步骤移到有卡阶段。
- 写启动/停止/健康检查/下载恢复脚本。
- 在本地进行网关单元测试、ArcReel 端点定义校验和请求预览。
- 生成权重清单、版本锁、磁盘预算、下一阶段测试单。

### 2.2 无卡禁止或必须推迟

- 不加载 Wan、H3 等大模型，不进行视频推理或大模型“CPU 试跑”。
- 不在 2GB 内存中转换/合并/量化大型权重。
- 不编译 FlashAttention、复杂 CUDA 扩展或大型推理引擎；必要时有卡再做，且先查是否已有 wheel。
- 不以无卡状态 `torch.cuda.is_available()==False` 判定 CUDA 安装损坏。
- 不因为暂时看不到 GPU，就把生产环境改成 CPU-only PyTorch。
- 不使用高并发下载、并行安装、多个远程 Agent 进程把 2GB 撑满。
- 不运行完整 ArcReel、数据库、ComfyUI 和 TTS 的常驻组合。

**无卡阶段只能承诺准备完成，不能承诺所有依赖都一定在 2GB 内安装成功。**部分源码包即使不需要 GPU，也可能因 CPU/内存限制需要延后。

Codex 建议在本地运行，通过 SSH 发命令；避免把 IDE Remote Server、语言服务器和 Agent 全部塞进无卡实例。不要全局关闭 CUDA，也不要往启动文件写入永久空的 `CUDA_VISIBLE_DEVICES`。

## 3. 执行前必须审计，不得覆盖现有成果

Codex 先读取现有项目，再执行 `scripts/preflight.py`。需检查并记录：

1. 本地 ArcReel 路径、Git remote、当前 commit、未提交改动、运行方式、项目数据位置。
2. AutoDL 是否为正确实例：Linux 发行版、CPU 配额、cgroup 内存上限、磁盘挂载、已有 Python 环境、已下载模型。
3. 现有 ComfyUI 工作流是否经过实际生成验证；先前聊天中的建议不算验证证据。
4. 端口、SSH 连接与已有服务，避免覆盖或结束不属于本方案的进程。
5. 生产化涉及 SQLite→PostgreSQL 或版本升级时，另做备份与迁移审批；不得在本轮静默改数据库。

允许先跑：

```bash
python3 scripts/preflight.py --root /root/autodl-tmp/arcreel-worker --out reports/preflight.json
```

脚本不扫描凭据、不安装依赖、不进行生成，仅写指定 JSON 报告。它只报告可读到的限制；无法确定容器上限时必须标为未知，而不是用物理主机总内存代替。

### 停止条件

实例不匹配、磁盘不足、未知用户数据覆盖风险、未确认收费操作、所选 wheel 不受宿主驱动支持、必需源码依赖不能在无卡完成、预定版本冲突时，应记录阻塞，不自行换平台/重装系统/充值/租更多机器。

## 4. 环境隔离与版本策略

### 4.1 基线候选

以下是**待目标机器验收的明确起始组合**，不是声称已经做过联合测试：

| 环境 | Python | PyTorch 组合 | 原则 |
|---|---|---|---|
| `video` | 3.12 | torch 2.11.0、torchvision 0.26.0、torchaudio 2.11.0，cu130 | ComfyUI 视频主环境；先原生注意力实现 |
| `tts` | 3.10 | torch 2.8.0、torchaudio 2.8.0，cu128 | CosyVoice 原生推理；不与视频环境混装 |
| `gateway` | 3.12 | 不安装 torch | 轻量 API、执行状态和文件传输 |

PyTorch 官网存在这些对应 wheel；它们的存在不证明 CosyVoice 所有旧依赖自动兼容。[S10] 对固定 ComfyUI commit 的要求如果不同，先核对证据、记录变更理由，再调整候选锁文件，不能默默追随 `latest`。

当前 ComfyUI 文档对 NVIDIA 新环境建议 cu130 路线，原先笼统的“CUDA 12.8 以上就行”不足以作为当前安装指令。[S09] CUDA 13.x 的驱动兼容基线为 580 分支或更高，但具体构建、PTX 和扩展还可能有更细条件。[S11]

- 驱动是平台宿主负责的层。**禁止在租赁容器内自行卸载/安装 NVIDIA 驱动。**
- `nvidia-smi` 中 CUDA 版本描述驱动兼容能力，不等同于 Python 正在用的 CUDA runtime；同时记录 `torch.version.cuda`。
- 不要求各环境使用相同 CUDA runtime；同一宿主驱动可以支持多个兼容用户态环境。
- 无卡时不能完整验证宿主 GPU/driver；租前页面/平台信息与首次有卡探测联合确认。
- 暂不运行 `torch.compile`、TensorRT 引擎生成或高复杂度优化。先拿到无优化的正确结果，再用受控对比优化。

### 4.2 CosyVoice 的强制注意事项

本次核查时，上游 `requirements.txt` 仍包含 `torch==2.3.1`、`torchaudio==2.3.1`、cu121 索引以及 DeepSpeed/TensorRT 等条目。[S08]

所以不允许直接把整份 requirements 无审查地安装到新 GPU 环境，更不允许因此把已安装的兼容 PyTorch 降级。

Codex 应：

- 保留上游原文件，在本项目生成单独的 `requirements.inference.txt` 和约束文件。
- 从实际推理 import 路径确定必需依赖。只有确认为训练/可选优化的依赖才可以省略，不能盲目删包后掩盖错误。
- 显式固定 PyTorch/torchaudio 匹配版本；记录其他依赖与上游的差异。
- 不混装 `onnxruntime` 和 `onnxruntime-gpu`。ONNX/CUDA provider 不兼容时，核查对应版本；仅在模型官方路径允许时让小型前处理用 CPU，并在报告说明，不能把整个 TTS 静默改成 CPU。
- 依赖冲突持续存在时停止并提交最小冲突报告。不要同时安装另一个 TTS 当作完成本模型部署。
- 语音参考使用有授权的短音频，并保留准确转写；默认声音必须先经用户试听确认。

## 5. 目录、缓存和下载策略

AutoDL 数据盘通常挂载 `/root/autodl-tmp`；关机后保留，但**不随系统镜像自动保存**。系统盘、数据盘的备份/迁移必须分别处理。[S02][S12]

```text
/root/autodl-tmp/arcreel-worker/
├── repos/                   # ComfyUI、CosyVoice，上游保持尽量干净
├── envs/                    # video / tts / gateway，独立前缀
├── models/                  # 只存一份权重，通过路径配置或链接复用
│   ├── wan22_i2v/
│   ├── cosyvoice3/
│   └── minimax_h3/           # 未经批准不下载增强模型
├── workflows/               # 已固定 API 格式工作流 + UI 原稿
├── manifests/               # 版本、文件、哈希、许可证与容量
├── adapter/                 # 轻量远端服务，不管理剧集业务
├── config/                  # 非密钥配置；密钥文件排除版本控制
├── cache/                   # HF、pip、torch、conda 缓存
├── tmp/                     # 下载临时文件、安装临时文件
├── runtime/                 # 远端任务执行账本、PID、锁
├── inputs/
├── outputs/                 # 按 job_id 分开，不互相覆盖
├── logs/
├── reports/
└── scripts/
```

环境变量写入本项目专用配置，不覆盖用户全局设置。根据所选工具设置：`HF_HOME`、`HF_HUB_CACHE`、`XDG_CACHE_HOME`、`TORCH_HOME`、`PIP_CACHE_DIR`、`TMPDIR`，以及使用 Conda 时的 `CONDA_ENVS_PATH`、`CONDA_PKGS_DIR`。

### 模型清单与下载门禁

在下载大文件前生成 `model-manifest.json`，每个文件至少记录：

- 模型逻辑 ID、发布组织、官方 repository、不可变 revision；
- 相对路径、预期字节数、发布方可获得的哈希/对象标识；
- 本地目标路径、下载状态、下载后流式计算的 SHA-256；
- 所需工作流、用途、许可证引用。

不要虚构 SHA-256，不要把未知 revision 填成猜测值。发布方没有 SHA-256 时说明校验等级；不能把自行算出的哈希说成已与官方核对。

Wan 只下载**I2V 高噪声专家、低噪声专家、文本编码器、对应 VAE及固定模板确实需要的附属文件**。从官方原生模板提取准确文件名，不凭记忆拼文件名。不得对拥有多种模型版本的大仓库直接无过滤全量 `snapshot_download`。[S05][S06]

H3 只取 **FL2VA 剪枝 INT8、配套 Qwen3-VL 编码器、视频/音频 VAE** 和所选模板真实依赖。模板扫描可能要求 LoRA/embedding；应核实后补齐或明确修改模板，并保存差异。第一轮不同时取 Ref2VA 与原始 BF16 大权重。[S07]

下载采取单进程、低并发、断点续传、临时文件完成后原子改名。哈希校验按块读取，不能 `read_bytes()` 一次载入几十 GB。

容量门禁：预计新增文件 + 下载/安装临时空间 + **至少 30GB 或全盘 15% 的较大者**保留空间，必须小于实际可用空间。已有文件校验通过就复用，不重复缓存后再复制出第二份。不随意清理用户旧文件。

## 6. 轻量执行服务与 ArcReel 适配

### 6.1 默认不新增第二套业务系统

远端使用轻量 FastAPI/同类服务 + SQLite 执行账本 + 进程级锁即可，不先引入 Redis、Celery、RabbitMQ、K8s。AutoDL 是容器实例，部署方案不依赖嵌套 Docker 或 systemd；使用独立 Python 环境和明确 PID 的进程管理。已有环境支持哪些启动机制，先审计再用。

推荐接口语义（**这是拟实现契约，不是声称 ArcReel/ComfyUI 已内置这些路径**）：

| 接口语义 | 行为 |
|---|---|
| health/live | 进程是否存活，不加载模型 |
| health/ready | 有卡是否可执行、模型文件是否就绪、全局队列是否可接收 |
| 提交视频 | 限定 model_id、workflow_id、素材 ID、参数；返回 job_id |
| 查询任务 | 返回 queued/running/succeeded/failed/unknown 等及可取回产物 |
| 取回产物 | 鉴权下载，带长度/内容哈希；不泄露任意本地路径 |
| TTS | 按 ArcReel 当前 AudioBackend 所需协议适配；不能只改成模型名就当兼容 |

源代码、端点格式与 ArcReel 固定版本的实际协议优先；通用 JSON 视频任务可用声明式端点，复杂上传、鉴权和 TTS 则用最小后端适配。[S03]

### 6.2 必须实现的执行规则

- **全局 GPU 槽=1**：视频、TTS、未来图片都共用。只锁提交过程不够，必须覆盖整个模型执行期。
- **批内复用模型，跨模型释放**：同模型可以连续生成；切换模型要实测内存回落，必要时退出对应 worker 子进程。停止空闲服务不影响项目数据。
- **幂等键**：同一请求重发返回原 job_id。素材或提示词真正修改时，必须创建新版本/新任务。
- **不承诺不可能的 exactly-once**：网关在提交 ComfyUI 成功却未写回 job_id 的窄窗口崩溃时，应通过持久请求标识/Comfy 元数据核对；无法确认则标 unknown/待人工核对，不能盲目重复生成。
- **离线不假排队**：无 GPU/服务关闭时返回明确状态；由本地保留待执行工作。已接受任务必须持久化，不能返回虚假成功。
- **超时分类**：网络断开不等于生成失败。先查远端历史；仅下载失败应继续取回，不重新推理。
- **成果归档**：远端成功与本地归档分成两个状态；本地校验视频/音频可读且哈希匹配后再确认回收完成。
- **有限重试**：同一明确原因的失败最多尝试一次修复复测；质量重抽默认不自动执行。
- **禁止绕过队列**：正式生产时不得在公开 ComfyUI 页面上另起任务绕过全局槽。诊断页面仅管理时使用。
- **模板白名单**：不接受任意用户 workflow 执行，不将不可信字符串插入 shell，不允许任意文件路径/URL下载。

### 6.3 音轨处理

- Wan I2V 走独立 TTS/后期声音路线，不承诺原生生成对白。
- H3 生成有声镜头时保留原始音轨，使用 FFprobe 检查是否存在真正的 audio stream；静音占位轨不能算通过。
- 每镜头明确 `保留原声 / 独立旁白 / 审核后混音`，不能把相同对白重复叠加。
- 姓名、数字、专有词和跨镜头音色需要人工验收。H3 通过安装不等于逐字台词已经合格。
- 修改一句旁白不重新生成全部视频。字幕以定稿台词为内容依据，时间依据实际音频，不靠猜测。

## 7. 安全连接与 Windows / Docker 注意事项

远端网关、ComfyUI、TTS 默认监听 `127.0.0.1`；SSH使用独立密钥，核对host key，不关闭主机身份校验。

ArcReel 若在 Docker 内，**容器内的 localhost 不等于 Windows 宿主 localhost**。[S13] 不能只在 Windows 浏览器测试通了就认定服务端也能访问。

推荐实现：在 ArcReel 同一 Compose 网络加入轻量 SSH tunnel sidecar，将远端 `127.0.0.1:9000` 转到该容器端口；不映射到 Windows 公网接口。ArcReel 使用同网络服务名访问。隧道 sidecar 可以在容器内部绑定 0.0.0.0，但不能发布到宿主公网。若沿用宿主隧道，则必须实测 `host.docker.internal` 等路由及防火墙，不能猜测。

最终必须从**ArcReel 后端容器**验证：健康检查、提交任务、取回素材三个动作。结果 URL 也须对取件方可达，并正确携带鉴权；不能让 AutoDL 请求本地 Windows 文件路径。

Codex 在本地使用现有 SSH alias。只需要非秘密的主机信息与授权，不要求用户把密码/私钥复制进文档。MCP 仅用于有需要的创作操作，部署和轮询尽量由脚本实现，避免重复消耗模型推理。[S14]

## 8. 分阶段任务及明确停止点

### 阶段 A：本地 + AutoDL 无卡准备

A1. 只读审计，建立变更清单，保护现有文件与运行服务。  
A2. 固定 ArcReel/ComfyUI/CosyVoice 源码版本或现有版本，生成环境候选锁、模型下载清单。  
A3. 下载主线权重与 wheel，低内存可完成的依赖先安装；有编译或内存阻塞的条目单独登记。  
A4. 本地完成适配服务逻辑、工作流字段映射、模拟端点与静态测试，所有模拟产物明确标记，不登记为真实生成。  
A5. 输出 `PRE_GPU_REPORT.md`、版本锁、模型清单、`GPU_TEST_PLAN.md`。

**到此必须停止。**不擅自开启 GPU，不向云端视频 API 发送真实生成，不开始 H3 大下载，不自动改计费或增加磁盘。用户在控制台切换正常模式并确认测试额度后才继续。

`PRE_GPU_REPORT.md` 必须区分：已下载、已安装、待有卡安装、待真实推理、明确阻塞。不得笼统写“环境100%完成”。

### 阶段 B：有卡环境检查与主线验收

用户关机后正常模式开机。初始建议给这一批**90分钟 GPU 开机窗口**，这是支出上限建议，不是保证90分钟完成所有工作。实际额度由用户确认。

执行顺序：

1. `nvidia-smi` 与 cgroup 核对硬件；在 video/tts 环境分别运行小型 GPU 探针，检查 CUDA版本、BF16/FP16/SDPA。
2. 补齐无卡阶段无法完成但推理确实必需的安装；没有必要不要编译加速库。
3. CosyVoice 用1段10～20秒中文，包含人名、数字与停顿，生成 WAV并试听。
4. Wan 使用官方模板有效参数，先生成约3～5秒单镜头、batch=1；从模板允许的较低分辨率验证，再验证目标720p档。禁止把违规帧数/尺寸硬塞入模型。
5. 连续执行 `TTS → Wan → TTS`，验证资源切换，不只测单模型。
6. ArcReel 实际提交视频和配音，结果出现在正确镜头；测试一次重取文件和一次SSH中断后的任务找回。
7. 回收文件、更新报告，通知用户结束/关机；仅停止进程不等于停止租赁计费。

GPU探针命令示例（每个环境单独执行）：

```bash
/path/to/env/bin/python scripts/gpu_probe.py --authorize-gpu --out reports/gpu-video.json
```

它只证明小型运算可执行，不证明 Wan、H3或所有第三方算子可用。

### 阶段 C：H3增强验证

- 主线已经通过，且用户明确批准本阶段的磁盘/测试额度。
- 许可证与目标部署/发行地区确认。H3采用自定义社区许可，当前有地域及商业条款，包括对输出使用的限制；不能只凭GPU在国内就默认任何发行地区都授权。[S15]
- H3新增准备仍优先放到无卡阶段；只下载一套FL2VA量化模板依赖。
- 单独验证3～5秒或模板允许的短时长有声镜头、音频解码、ArcReel原音保留。
- 标准采样作为质量基准；Turbo只能作为另一个预览档，经质量对比后启用，不能将加速档通过等同于最高质量通过。
- H3失败不破坏已通过的Wan/CosyVoice环境；报告不可用范围，不继续无预算排错。

### 阶段 D：生产准入

用30～60秒真实短剧片段验收，记录每镜头模型、工作流、输入资产版本、耗时和人工采用结果。至少检查：

- 同角色多镜头；
- 正式旁白准确性与音色一致；
- 局部重做不覆盖历史结果；
- 重启后已确认产物和远端任务能找回；
- 导出成片声音正确、字幕可读；
- 一次实际备份/恢复检查。

**没有正面说话镜头，就不能声称口型能力已经通过。没有真实图像生成，就不能声称自建出图已经通过。** 验收报告按实际覆盖能力列出，不夸大范围。

## 9. 监控与止损

采集冷启动、权重加载、推理、VAE解码、切换模型全过程：

- 主机可用内存与cgroup使用/上限/峰值；
- `memory.events` 的OOM变化；
- 全卡显存和各PID占用；
- 每阶段wall time、请求/队列/远端ID；
- 文件大小、输出分辨率、音轨、时长、工作流哈希。

初始容量目标：让真实峰值保留约15%～20%余量；这是本方案安全预算，不是官方模型指标。PyTorch的显存计数只覆盖本进程分配，不代表整卡全部占用。

止损要求：

- 不使用while true自动生成重试；同一错误最多一次有理由的修复复测。
- 磁盘将不足、宿主内存即将越界、加载明显反复抖动时，取消新任务，保留证据，不暴力清缓存或杀全机进程。
- 90分钟等预算到期前应预留取件时间；未完成下一项不继续提交。记录完成到哪一步，征询是否续时。
- 默认不自动控制AutoDL账户。需要自动关机时，必须获得授权并先核查队列、运行进程、成果归档、日志已落盘；不可只设置裸 `sleep; shutdown`。
- 即使只执行到阶段B，也要在控制台确认实例状态，否则GPU仍可能收费。

成本统一使用实际页面单价：

`费用 = 正常模式开机小时 × GPU实例小时价 + 无卡小时 × 无卡小时价 + 付费磁盘/其他实际费用`

统计包含加载与空闲，不只统计采样时间。扩容磁盘关机仍可能收费；系统镜像不代替数据盘备份。[S01][S02][S12]

## 10. 日常生产与迁移

日常：本地确认剧本和参考图 → 汇集待生成批次 → 正常开机 → 健康检查 → 同模型成批生成 → 成果回本地 → 审核 → 结束生产窗口并确认关机。

无卡模式用于下载/维护，不是完整模型服务运行模式。无卡释放GPU可能导致下次无可用卡，紧急交付需提前锁定实际生产窗口。[S01]

备份：

- 本地：ArcReel数据库 + 项目媒体 + 版本/配置 + 已授权声音参考；
- 云端：执行账本、工作流、依赖锁、日志摘要、尚未取回输出；
- 系统镜像与数据盘分开保护，不能把 envs 放数据盘后又以为保存系统镜像包含全部环境。[S12]

迁移顺序：冻结新任务 → 回收产物并备份 → 新机恢复代码/数据 → 调整连接地址 → 跑同一组最小回归 → 切换 → 确认无缺失后再释放旧实例。未经许可不销毁旧机。

## 11. Codex最终应交付的文件

```text
reports/INVENTORY.md
reports/PRE_GPU_REPORT.md
reports/GPU_TEST_PLAN.md
reports/GPU_TEST_REPORT.md
reports/ARCREEL_INTEGRATION_REPORT.md
reports/COST_AND_QUALITY.md
manifests/environment.lock.json
manifests/model-manifest.json
workflows/<固定模板>.json
scripts/prepare_nogpu.sh
scripts/start_worker.sh
scripts/stop_worker.sh
scripts/healthcheck.sh
scripts/run_smoke_tests.sh
scripts/sync_artifacts.sh
README_OPERATIONS.md
```

这些文件由 Codex 根据真实环境生成；本交付包不伪造它们已完成。

报告每项状态只允许：`已准备 / 已安装 / 已实测通过 / 未验证 / 阻塞 / 不适用`。重要结论附实际命令、版本、输出摘要和文件路径。不得输出凭据或用户素材中的敏感内容。

## 12. 权限和变更约束

- 不重装系统，不修改宿主驱动，不修改计费方式，不租多卡，不自动充值。
- 不删除已有模型、用户项目或历史产物；不运行大范围清理命令。
- 不升级覆盖现有生产环境；新建隔离环境，先备份后变更。
- 不额外接入收费API或下载不在清单内的大模型。
- 不将原始数据库、声音参考等私密数据上传到第三方模型服务做诊断。
- 不为“速度”关闭TLS校验、SSH host key校验、模型许可证限制或服务鉴权。
- 不把排查失败说成硬件已经不够，也不把某个演示通过说成所有生产任务通过。

## 13. 官方资料与证据

以下页面为2026-09-08核查入口；执行时应再次读取并将最终使用版本固定。这里的参考链接不是安装时允许无限追随main的理由。

[S01] AutoDL 无卡模式/省钱绝招：
`https://www.autodl.com/docs/save_money/`

[S02] AutoDL 本地数据盘与计费：
`https://www.autodl.com/docs/local_disk/`

[S03] ArcReel 供应商、TTS及自定义端点：
`https://docs.arc-reel.com/guide/providers/`

[S04] ArcReel 架构、独立并发、持久任务、版本与音轨：
`https://docs.arc-reel.com/dev/architecture/`

[S05] ComfyUI Wan2.2 原生工作流：
`https://docs.comfy.org/tutorials/video/wan/wan2_2`

[S06] Wan2.2 官方仓库和 ComfyOrg 权重：
`https://github.com/Wan-Video/Wan2.2`
`https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged`

[S07] ComfyUI MiniMax H3 原生工作流：
`https://docs.comfy.org/tutorials/video/minimax/minimax-h3`
`https://huggingface.co/Comfy-Org/MiniMax-H3`

[S08] CosyVoice 官方仓库、依赖和权重：
`https://github.com/FunAudioLLM/CosyVoice`（当前访问可重定向到QwenAudio/CosyVoice；核对官方归属）
`https://raw.githubusercontent.com/QwenAudio/CosyVoice/main/requirements.txt`
`https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512`

[S09] ComfyUI 当前安装说明：
`https://github.com/Comfy-Org/ComfyUI`

[S10] PyTorch 官方版本/安装组合：
`https://pytorch.org/get-started/previous-versions/`

[S11] NVIDIA CUDA/Driver兼容说明：
`https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html`

[S12] AutoDL 系统盘、数据盘与镜像：
`https://www.autodl.com/docs/env/`
`https://www.autodl.com/docs/image/`

[S13] Docker Desktop 网络边界：
`https://docs.docker.com/desktop/features/networking/`

[S14] Codex MCP 官方说明：
`https://developers.openai.com/codex/mcp`

[S15] MiniMax H3 社区许可证：
`https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE`

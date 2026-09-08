# ArcReel 云端媒体执行环境

## 当前部署边界

- 本地 ArcReel 已有服务继续使用：后端 1241、前端 5173。正式数据位于 `../ArcReel/deploy/projects`。
- Wan2.2 I2V-A14B FP8 与 CosyVoice3 均部署在 AutoDL。取消本地 TTS 路线。
- 远端根目录：`/root/autodl-tmp/arcreel-worker`。当前是系统盘 overlay，不是独立数据盘；系统重置/迁移前必须单独回收文件。
- AutoDL 当前 0.5 核 CPU、2GB 内存，无 GPU；准备脚本不切换模式、不运行模型。
- H3 不在本轮部署范围。
- 云端 CosyVoice 候选环境使用 CPU 版 ONNX Runtime 执行语音前处理，主 PyTorch 模型要求 GPU；前处理耗时和兼容性仍需有卡阶段核实。
- 本地重新启动请使用 `media-worker/start-local-cloud.ps1`。它在 ArcReel 进程中继承外部代理并绕过 localhost/127.0.0.1/::1，避免 SSH 隧道请求被 Windows 系统代理转发。无需改动 Windows 全局代理或 ArcReel 的 .env。

## 下载与安装

远端 `scripts/prepare.py` 下载固定 ComfyUI 源码和四个 Wan 权重；`scripts/finish_prepare.py` 等待同一个准备锁，下载 CosyVoice 后调用 `scripts/install.py` 建立 video/tts/gateway 独立环境。下载顺序执行，按发布站点提供的文件 SHA-256 校验。GPU PyTorch 使用官方带哈希的 wheel 链接，普通依赖使用阿里云 HTTPS PyPI 镜像。

源码提交见 `source-lock.json`；模型清单见 `wan-models.json` 和 `reports/cosyvoice-model-manifest.json`。权重下载使用 ModelScope 中对应仓库；哈希是 ModelScope 提供的校验值，本轮未逐文件与 Hugging Face LFS 对象标识交叉比对。

WeText 的 5 个规则文件也已固定版本并校验，清单在远端 `manifests/wetext-models.json`。`offline_text.py` 仅将 WeText 的资源查找定向到本地，保留原文本规范化实现；启动时验证哈希，避免首次开卡再拉取资源。

只查看进度：

```bash
tail -c 1200 /root/autodl-tmp/arcreel-worker/logs/prepare.log
tail -c 1200 /root/autodl-tmp/arcreel-worker/logs/finish_prepare.log
tail -c 1200 /root/autodl-tmp/arcreel-worker/logs/install.log
cat /root/autodl-tmp/arcreel-worker/reports/preparation-status.json
```

文件不存在表示对应步骤尚未到达，不表示通过。安装错误保留在日志和状态报告；不要无限重试。

## GPU 验收前

在控制台确认实际 GPU、显存、内存和本次开机额度。分别运行 `gpu_probe.py --authorize-gpu --out <报告路径>`。确认 video 环境 cu130 的宿主驱动兼容性，不在容器安装驱动。

只有这些检查通过才执行：

```bash
bash /root/autodl-tmp/arcreel-worker/scripts/start_worker.sh --authorize-gpu
```

本地运行 `tunnel.ps1`，通过 SSH 将本地 `127.0.0.1:19000` 转到远端 `127.0.0.1:9000`。密码交互输入，不保存到脚本。

## ArcReel 接入

已创建自定义供应商 `AutoDL Wan / CosyVoice (待 GPU 验收)`，ID 1，URL 为 `http://127.0.0.1:19000`。两个模型均禁用，尚未修改现有默认模型。

- 视频模型 `wan22-i2v-fp8`，端点 `newapi-video`，支持本轮 3/5 秒固定模板；标准 20 步，无 Turbo LoRA。
- 音频模型 `cosyvoice3`，端点 `openai-tts`，返回 WAV。
- 同一云端网关共用一个 GPU 锁。视频排队；GPU 忙碌时同步 TTS 返回 429，不接受虚假排队。
- TTS 每次通过独立子进程运行，执行后释放资源；切回视频需重新启动/加载 ComfyUI。第一版偏重可验证的资源释放，尚未优化声音模型批内常驻。
- 模型通过真实试生成与回收后才启用，并选择默认音频/视频模型。

API 密钥已保存在远端权限为 600 的 `config/gateway.key` 和 ArcReel 供应商配置中，不在报告显示。

## 声音配置

首次试跑使用固定版本 CosyVoice 源码中的公开示例音频，标记 `demo_only`，映射到 `alloy`。准备命令为 `envs/tts/bin/python scripts/prepare_demo_voice.py`（在远端根目录执行）。正式声音尚未选择；将有授权的参考音频放入远端 `inputs`，在 `config/voices.json` 配置音色 ID、相对参考路径和准确转写。

```json
{"alloy":{"label":"经试听确认的默认旁白","reference":"voices/narrator.wav","transcript":"参考音频的准确文字"}}
```

`alloy` 在这里仅为兼容现有 ArcReel 音色选择器的 ID，不代表使用 OpenAI 的 Alloy 声音。未配置的音色返回明确错误。多角色自定义音色目录尚需后续前端/后端适配，不能将该默认音色接口称为完整角色配音系统。

## 开卡后的适配器试跑

无卡准备检查：在远端根目录执行 `/root/miniconda3/bin/python scripts/preflight_cloud.py`，结果在 `reports/preflight.json`。它检查文件大小、安装后的导入和声音配置，不构造模型。

GPU 检查及网关启动后，Windows 保持 `tunnel.ps1` 运行，在项目根目录执行：

```powershell
.\ArcReel\.venv\Scripts\python.exe media-worker/smoke_arcreel.py --authorize-gpu --image "已确认首帧的绝对路径.png"
```

脚本依次调用 ArcReel 的真实音频适配器、视频适配器、再次配音，并用本地 FFmpeg 生成带声音预览。默认 3 秒 480p 档，可用 `--resolution 720p --aspect 9:16`。ArcReel 8 像素对齐的宽高会在网关保持比例、向下对齐到 Wan 所需的 16 像素：本版本 480p 横屏请求为 896×504，实际测试输出为 768×432；720p 的 1280×720 无须调整。实际尺寸写入任务 metadata。

结果在 `media-worker/outputs/smoke-时间戳`。这一步不修改生产默认模型，也不替代项目界面的镜头归属、版本和任务账本验收。

## 任务恢复和停止

同输入、参数和工作流返回同一视频任务；修改输入或 seed 创建新任务。重启后 running 标为 unknown，避免盲目重做。queued 任务在经过 GPU 授权的启动中恢复。

`unknown` 必须查看 Comfy history 的 `arcreel_job_id` 和执行日志后人工核对，不自动重发推理。已完成产物不自动删除。ArcReel 的重取件仍走原任务。

队列与 GPU 均空闲时执行 `stop_worker.sh`。仅停止进程不会停止计费，必须在 AutoDL 控制台关机。

## 已验证与未验证

- 已通过：全部白名单文件下载校验、三个环境安装及依赖一致性、关键源码导入、离线分词与数字规范化、参考音频读取、本地服务健康、供应商配置读取、真实 SSH 隧道鉴权；网关 11 项无模型契约测试。
- 未验证：目标 GPU 驱动、实际模型推理、速度与质量、ArcReel 真实视频/音频回收和项目成片。
- 本地 TTS 已停止。自动审批阻止删除本轮新建目录；本地 `envs/tts`、`models/cosyvoice3`、`repos` 与源码压缩包仍可能占用空间，不是正在使用的生产服务。

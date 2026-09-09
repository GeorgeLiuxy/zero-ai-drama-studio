# H3 人物对白接入与单镜头验收

记录日期：2026-09-08。四份 H3 权重的准备状态为 `download_verified`，原生节点结构检查已通过。H3 网关适配器、提示词转换器及项目模型配置已准备；云端部署是否完成，以 `reports/h3-dialogue-deploy.json` 和当前服务检查为准。

**2026-09-08，正式 E2S05 样片已生成并通过技术链路验收；台词听感、口型和动作自然度仍待用户审核。** 此次为 H3 原生音画，不是之前 Wan 配 CosyVoice 的旁白样片。

- 正式任务 `f5384fe4effa472b851fb735d722f118`，已写回第 2 集 E2S05 的视频 v1；当前性与来源均验证通过。
- 576×1024、124帧、24fps、5.166667秒；32kHz双声道原生声音，完整视频和音频解码通过。
- 正式预览识别 `character_speech`，保留视频声音、增益1.0，独立旁白为空；可编辑导出包视频与原视频 SHA-256 完全一致。字幕来自剧本，当前是机械时间轴，不是语音识别验词结果。
- 首次任务从开始到回收231.28秒，其中ComfyUI执行217.29秒；观察到显存约40.4GiB，未做连续峰值测量，后续热启动速度尚未测量。
- 视频SHA-256：`90670306482bbb9d41c19fc4fedf746a770109f34e9998eea16e5ec157f3a2ef`。
- 技术报告：`reports/h3-formal-validation.json`；脚本意图/任务记录：`reports/h3-formal-sample.json`。项目中已有视频时，不要删除该记录重复提交。

**2026-09-08 20:09，首尾帧（FLF）完成云端 CPU 部署和项目配置发布。** 本地完整操作测试115项通过，39个子测试通过；云端隔离预检通过。该 CPU 部署阶段的生产队列为4个历史成功任务，未新增推理。项目能力接口已回读确认 `first_frame=true`、`last_frame=true`、`generate_audio=true`，5秒与576档保持不变。部署与配置记录在 `reports/h3-flf-deploy.json`、`reports/h3-flf-config.json`。

**2026-09-08 20:28，正式 E1S01 首尾帧 GPU 技术样片成功。** 截至此次验收检查，服务已获得 GPU 授权、处于 ready 且空闲状态；这项状态是检查时的快照。

- 正式任务 `444a8553434644c182e63acde7de90ed`，已写回 E1S01 视频 v1；任务耗时261.925331秒，其中 ComfyUI 执行223.47秒。
- 实际画面576×1024、124帧、24fps、5.166667秒，含32kHz双声道原生音轨；完整音视频解码通过，正式资产当前性与来源验证通过，可编辑导出包视频与项目视频 SHA-256 一致。
- E1S01 是旁白镜头，采用 `post_production`，本次未合成独立旁白。正式预览识别 `narrator_voiceover`，保留视频原生音轨，独立旁白为空；字幕来自脚本，不是 ASR 识别结果。
- 原生音轨虽存在且可解码，测得峰值仅0.000470142（约−66.6dBFS），音量很低，不能据此认定环境音质量通过验收。
- 技术报告：`reports/h3-flf-validation.json`；GPU、音量与三帧视觉抽查记录：`reports/h3-flf-gpu-validation.json`。已有成功任务，不要重复提交。

## 当前接入合同

| 项目 | 当前合同 |
| --- | --- |
| 网关模型 ID | `h3-i2v-int8` |
| ArcReel 模型 | `custom-1/h3-i2v-int8` |
| 端点定义 | `arcreel-h3-endpoint.json`；当前注册键 `ce-2`，重新部署时以配置报告中的键为准 |
| 输入 | 一张正式首帧、结构化视频提示词，可选一张已审核尾帧；单次一段视频 |
| 分辨率设置 | `576` 表示当前原生短边档位；9:16 为576×1024，16:9 为1024×576，1:1 为576×576 |
| 时长 | 只开放名义5秒；124帧，24fps，实际约5.1667秒 |
| 音轨 | 原生联合生成，`audio_track=always_on`；请求不能关闭原生音轨 |
| 角色音色 | `character_voice_binding=prompt`，只使用角色声音文字描述 |
| 当前未接入 | 3秒、参考音频、额外人物参考图、音色克隆、硬性音色绑定、跨片段稳定音色保证 |

同目录归档的 Wan 首尾帧工作流和 `arcreel-wan-endpoint.json` 已通过本地接口合同测试，但尚未通过 GPU 生成验收。当前正式 Wan 端点 `ce-1` 仍只接首帧；归档这些文件不会修改端点配置或部署云端，也不能据归档内容认定正式系统已经支持尾帧。

单首帧继续使用 `workflows/h3-api.json`；有尾帧时使用独立 `workflows/h3-flf-api.json`，复用现有四份权重，不需要另下载一个模型。旧工作流文件、单首帧提示词版本和任务哈希保持不变，不能为启用尾帧改写历史单首帧任务。

存储的 `workflows/h3-api.json` 仍可看到最初的864×480准备参数。真实提交时网关会覆盖为上表画布，不应据模板默认值判断成片比例。首帧和可选尾帧的比例均须严格匹配请求画布：576×1024、1024×576或576×576。比例不一致返回422；不得通过拉伸或悄悄裁剪来绕过检查。576×1024是原生推理尺寸，不是720×1280。

H3 的视频与音频潜变量共同采样，经视频和音频 VAE 分别解码，再进入 `CreateVideo`。下载和保存应保留完整原生音轨。后续配画外旁白时，应在时间线混音，不得用旁白替换人物对白，也不得通过 `-shortest` 或截断至整5秒来掩盖时长差异。

## 提示词转换

云端 `scripts/h3_prompt.py` 提供 `format_h3_prompt()`，格式版本为 `arcreel-h3-dialogue-v1`。网关环境需要 `PyYAML==6.0.3`。转换发生在入队之前，原始提示词、转换结果和格式版本随任务记录保存并进入任务哈希；模型清单和工作流的哈希也进入任务身份。

有尾帧时使用独立版本 `arcreel-h3-dialogue-flf-v1`：Picture 1 对应0秒，Picture 2 对应最后一帧的时间 **5.125秒**，即 `(124 - 1) / 24`。5.166667秒是整段播放时长，不能拿它代替尾帧坐标；转换器会拒绝这种误传。提示词描述从首帧经连续动作自然到达尾帧，仍保留逐字对白和原生声音策略。无尾帧的调用及转换结果字节不变，已用历史输出哈希回归验证。

输入使用 ArcReel 当前渲染的标准 YAML：`Action`、`Camera_Motion`，以及可选的 `Voice_Profiles`、`Ambiance_Audio`、`Dialogue`。末尾独立的固定否定句“禁止出现：BGM、文字字幕、水印。”由转换器处理。不能将网页复制的散文或已经手工编译的 H3 提示词直接替换这个输入合同。

转换结果包含首帧映射说明和三个固定字段：

- `integrated_multimodal_description`：角色动作、镜头、声音描述和有序对白。
- `overall_soundscape`：环境音与动作声音，不重复对白。
- `non_diegetic_music: N/A`：不生成背景配乐。

角色按对白首次出现顺序分配 `(S1)`、`(S2)`，同一镜头内重复发声复用同一 ID。台词保留原文，包括标点和空白，例如 `<d>[Chinese]谁要造反？</d>`；人物、情绪和音色说明放在标记外。没有 `Dialogue` 时明确禁止角色说话并要求闭嘴。角色 ID 不是跨镜头声纹锁定。

转换器拒绝重复 YAML 键、显式类型标签、锚点和别名、过深结构、未知字段以及格式不识别的自由文本；拒绝时不能提交 GPU 任务。这样可以避免把带台词的输入错误识别成无对白视频。

## 验收前检查

脚本支持两种位置：项目旁的 `media-worker/` 和仓库内的 `ArcReel/deploy/media-worker/`。后者是版本归档；从仓库根目录可用 `.venv/Scripts/python.exe deploy/media-worker/configure_h3_dialogue.py inspect` 读取当前项目配置。报告、密钥、输出和备份不随代码归档；涉及既有样片的轮询与对账应继续使用原工作目录中的报告，不能因归档目录没有报告就再次提交。归档目录不是当前云端运行状态的证据。

在本地 PowerShell 中，从 `D:\projects\SELF\zero evo` 执行以下只读检查；登录信息由现有本地配置读取，不应写进命令或报告：

```powershell
& '.\ArcReel\.venv\Scripts\python.exe' '.\media-worker\configure_h3_dialogue.py' inspect
Get-Content -LiteralPath '.\media-worker\reports\h3-project-config.json'
```

本次配置报告指向模型 `custom-1/h3-i2v-int8`、项目 `proj-8fbe5aa8` 和配置前备份。只有需要安装或恢复这套配置、且项目无运行或排队任务时，才运行配置模式；已经配置完成时无需重复运行：

```powershell
& '.\ArcReel\.venv\Scripts\python.exe' '.\media-worker\configure_h3_dialogue.py' configure
```

云端部署完成后，应存在本地 `reports/h3-dialogue-deploy.json`，其中记录部署时间与远程备份位置。没有这份报告时，检查部署过程和实际文件，不能仅凭模型选择框判定成功。

上述是原单首帧接入记录。此次 FLF 更新应另外核对 `reports/h3-flf-deploy.json` 与 `reports/h3-flf-config.json`，其中的实际配置和备份路径由部署完成后填写；不要先把待生成报告当成成功证据，也不要重跑旧 `configure_h3_dialogue.py configure` 来代替新的 FLF 配置核对。

在已有 SSH 会话中执行以下检查，不触发生成：

```bash
cat /root/autodl-tmp/arcreel-worker/reports/h3-preparation.json
cat /root/autodl-tmp/arcreel-worker/reports/h3-environment.json
/root/autodl-tmp/arcreel-worker/envs/gateway/bin/python -c 'import yaml; print(yaml.__version__)'
curl --fail --silent http://127.0.0.1:9000/health/live
curl --fail --silent http://127.0.0.1:9000/health/ready
```

无 GPU 模式只需权重/节点准备、依赖及网关存活检查。当前没有运行中的网关时，可用以下命令启动准备服务；如果已经有网关，不要重复启动：

```bash
bash /root/autodl-tmp/arcreel-worker/scripts/start_worker.sh --prepare-only
```

该模式只启动 gateway，不加载模型、不启动 ComfyUI、不接受 GPU 生成任务。`/health/live` 正常且 `/health/ready` 为 `gpu_authorized=false`、`ready=false` 是预期状态，并不是部署失败。

以后开启 AutoDL GPU 后，若无卡准备网关仍在运行，**先确认 `busy=false`、`queued=0`，停止它并等待记录的进程退出、9000端口停止监听**：

```bash
bash /root/autodl-tmp/arcreel-worker/scripts/stop_worker.sh
```

确认退出后，再检查显卡并启动 GPU 网关；不要在旧进程尚未退出时紧接着重复启动：

```bash
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv
bash /root/autodl-tmp/arcreel-worker/scripts/start_worker.sh --authorize-gpu
curl --fail --silent http://127.0.0.1:9000/health/ready
```

只有 GPU 网关 `ready=true`、`gpu_authorized=true` 且无活动任务时才提交验收片。切换实例硬件模式不会改变一个已运行进程的授权状态；`--authorize-gpu` 启动也不是提交样片，服务停止不等于停止 AutoDL 计费。

## 正式项目 E2S05 单镜头

验收对象为 `proj-8fbe5aa8` 的 `scripts/episode_2.json`，分镜 `E2S05`。当前脚本要求5秒，发声角色“沈家兄长”，唯一台词“谁要造反？”。使用该分镜已经审核的正式首帧，原生9:16画面，固定种子 `20260908`。若脚本、台词或已有视频资产发生变化，先检查新状态，不修改剧本以强行通过测试断言。

以下是原单首帧样片的首次提交入口，现已有成功任务和视频资产，保留作复现说明，**本次 FLF 更新无需再次执行**。它通过正式 ArcReel 任务入口写入台账及分镜资产，而不是只生成孤立文件：

```powershell
& '.\ArcReel\.venv\Scripts\python.exe' '.\media-worker\configure_h3_dialogue.py' submit
```

脚本先写 `reports/h3-formal-sample.json` 的提交意图，再发请求；已有报告会阻止再次提交。取得任务 ID 后，使用同一记录轮询：

```powershell
& '.\ArcReel\.venv\Scripts\python.exe' '.\media-worker\configure_h3_dialogue.py' poll
```

完成后分别记录技术检查和人眼、听感审核：

1. 正式分镜视频资产已写入，源文件能完整解码；画面576×1024、124帧、24fps，实际时长如实记录。
2. 音轨存在，能够解码，数值有限且非静音，音频与视频时长相符；导出环节保留原生音轨。
3. 听到的确实是“谁要造反？”，没有漏字、增词、重复、旁白抢读或末字被切掉。
4. 说话的是沈家兄长，嘴型与声音相符；未发声角色不会同步乱动嘴。
5. 身份、服饰、空间关系与首帧一致，手部、身体和镜头运动连续，没有明显跳变。
6. 记录加载与生成耗时、GPU/RAM情况，以及这一次样片的限制；单镜头通过后再评估多角色、跨镜头音色及整集工作流。

文件生成成功和“有声音”只满足部分技术条件，不能自动勾选人物对白质量验收。

## 首尾帧样片结果与后续验收

本次使用 **E1S01，属于旁白镜头**，核对其正式首尾帧、当前性、画布比例和5秒时长合同后，经正式视频入口完成了首尾帧 GPU 技术样片。首尾帧输入与正式资产写回已验证；旁白留待画外音流程合成，本次没有合成旁白，可见角色没有对白。

对视频第0、60、123帧的抽查呈现近景到远景，末帧构图与已绑定尾帧基本一致。但原提示词的 `Push In` 与首帧近景、尾帧远景方向冲突，本次未静默改写剧本。三帧抽查不能证明整段运动连续，仍需观看完整片段检查镜头、人物动作与闭嘴表现，并处理镜头指令和首尾构图的一致性。原生音轨峰值很低，环境音听感与可用性也尚未验收。

旧 E2S05 是成功的单首帧人物对白技术样片，E1S01 则验证了旁白镜头的首尾帧技术链路。**人物对白加尾帧的组合尚未验收。** 要验收两者一起工作，需要为合适的对白分镜明确绑定并审核尾帧，再提交一条有独立记录的新任务。审核时同时检查台词原文、发声人物、口型和连续到达尾帧的动作；不能用 E1S01 或旧 E2S05 的结果替代这项验收，也不能自动把其他图片当成尾帧。

## 失败、unknown 与重复提交

`queued`／`running` 表示已有任务；等待或检查原任务即可。`failed` 表示网关已得到明确失败或成片技术检查失败，应先看错误。`unknown` 表示提交、进程或响应状态不确定，云端可能仍在生成或已经生成成功。

遇到超时、网络中断、只有 `submit_intent` 的本地报告或 `unknown`：保留报告、任务 ID、固定种子和日志，不删除报告重提，不换种子绕过幂等性。先在项目任务列表核对提交时间和 E2S05 资源，再查云端任务及其 ComfyUI `remote_id`。以下云端查询只读取任务状态，不改队列或数据库：

```bash
python3 - <<'PY'
import json
import sqlite3
db = sqlite3.connect('file:/root/autodl-tmp/arcreel-worker/runtime/jobs.sqlite?mode=ro', uri=True)
db.row_factory = sqlite3.Row
rows = db.execute("SELECT id, status, remote_id, error, created FROM jobs WHERE status IN ('queued','running','unknown') ORDER BY created").fetchall()
print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
db.close()
PY
```

结合该 `remote_id` 对照 ComfyUI 历史、输出文件及 `logs/gateway.log`、`logs/comfy.log` 后再对账。若输出已经存在，应确认归属并恢复原任务结果；若确认没有远程执行且失败原因已修复，才安排一次有记录的新尝试。不要手动把 `unknown` 改成 `queued`。

## 精确回滚

原单首帧配置备份记入 `reports/h3-project-config.json`，云端备份记入 `reports/h3-dialogue-deploy.json`。本次 FLF 更新必须使用新 `reports/h3-flf-config.json`、`reports/h3-flf-deploy.json` 中对应这次操作的备份，不应恢复到更早整套部署。恢复前先保存当前状态并确认项目、网关都没有活动任务。

回滚以这一次操作实际触及的配置为范围：

- 对照该次 `project.json` 备份，恢复 `video_provider_i2v`、`video_generate_audio`、`character_voice_binding`，以及 `model_settings` 中 `custom-1/h3-i2v-int8` 这一项；保留其他模型配置和此后新增的分镜、首帧、视频资产。
- 对照备份数据库读取此前 provider 1 的模型设置，只撤销本次 H3 模型条目变化。若某个值后来又被用户修改，先核对差异，避免覆盖较新的配置。
- 端点若由本次新建，仅在确认无模型引用后删除；若本次更新了已有端点，恢复它此前的定义。不要假定 `ce-2` 在所有部署中都属于本次新建。
- 云端脚本按部署备份精确恢复此次修改的文件，检查启动依赖及服务状态。新增文件在旧备份中不存在时，应按部署清单处理，而不是递归覆盖整个目录。

**备份中的整份数据库用于核对和灾难恢复，不应直接覆盖仍在使用的数据库。** 整库回写会撤销之后的用户任务、配置和资产记录。同样，不要整份覆盖项目 JSON，也不要用旧 `jobs.sqlite` 清空新的任务历史。优先通过当前配置 API 应用经过核对的字段差异；不要输出备份中的认证信息。

## 上游依据

- [MiniMax H3 模型与 FL2VA／Ref2VA 规格](https://huggingface.co/MiniMaxAI/MiniMax-H3)
- [官方基础模式提示词规范](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_base_en.md)
- [本次固定版本的 ComfyUI H3 节点实现](https://github.com/Comfy-Org/ComfyUI/blob/5bbdf8a76678e2c7cfb519a49a9c3a7137fd6280/comfy_extras/nodes_minimax_h3.py)

上游完整模型的宣传范围大于当前接入范围；本项目以实际开放的端点合同和逐项验收记录为准。

## 本次能力发布与回滚

本次仅原地更新H3端点 `ce-2`，没有替换项目模型设置或用户首尾帧。发布前端点定义保存在 `backups/before-h3-flf-config-20260908-200908/endpoint.json`；回滚时只将其中 `definition` 通过正式端点更新API写回ce-2，不覆盖整个项目或数据库。云端旧代码备份为 `/root/autodl-tmp/arcreel-worker/backups/before-h3-flf-20260908-200702`，其manifest标记了此前存在的文件；回滚代码应先等待当前任务结束。

已经完成配置时无需重复发布。需要重放部署时，从工作目录执行 `uv run --no-project --with paramiko python media-worker/deploy_h3_flf.py`，SSH凭证交互输入；该命令只面向空闲无GPU授权网关。然后运行 `.\ArcReel\.venv\Scripts\python.exe media-worker/configure_h3_flf.py`，它核对部署报告与源文件摘要后才更新实际端点。启用GPU和提交生成是后续独立操作，不能用修改能力开关代替工作流部署。

# ArcReel 提示词环节盘点（待逐项审阅）

盘点日期：2026-09-09。性质：只读调查及索引，不是优化补丁，也不是已批准的修改方案。

本次没有修改提示词、应用配置、原文、正式剧本或素材，没有提交文本、图片、音频、视频生成任务。仅新增本文档。下一步由用户选择条目，先审阅现行完整提示词和实际输入，再讨论修改。

## 1. 盘点范围与当前路径

覆盖应用自有的 Agent 指令、文本生成模板、图片/视频请求拼装、条件启用的视觉检查、当前 AutoDL 适配脚本，以及影响模型理解的结构说明。SDK/模型内部不可见的提示词不在“已完整检查”范围内；不把测试样例、历史备份、第三方依赖中的任意 prompt 字样算成当前生产环节。

当前项目 `proj-8fbe5aa8` 的文件配置：剧情演绎 `drama`、小说 `novel`、分镜图生视频 `storyboard`、多宫格开启、9:16、角色声音绑定 `prompt`、旁白音色 ID `alloy`。

数据库当前默认 Agent 后端为 **Codex**。它的业务指令由 `server/agent_runtime/codex_instructions.py` 生成，`codex_client.py` 装配工具和项目模式。已有会话可以按历史记录恢复原来的后端，因此“系统默认”不等于“每个历史会话必然使用”。

当前项目的 `CLAUDE.md` 与仓库 drama 模板相同；但项目 `.claude/agents/`、`.claude/skills/` 对应旧文件已缺失，清单中有删除 tombstone，不能自动恢复。共用 `.claude/references/` 文本仍存在。**旧 Skill 文件仍在仓库，不代表当前 Codex Agent 会执行它；共用 reference 被 Python builder 读取，也不代表在调用旧子 Agent。**

当前业务主链：

```text
用户需求＋项目事实＋工具说明
       ↓
Codex Agent 业务指令（编排；部分内容分析和定点修订也由它完成）
       ↓
概述／分集／脚本规划的文本模型请求
       ↓
结构化内容确认（对白、旁白、分镜边界）
       ↓
视觉提示词编写的文本模型请求
       ↓
资产图／单张分镜图／多宫格图的图片请求
       ↓
视频动作＋对白＋声音风格的请求拼装
       ↓
可选视觉动作规划 → H3 格式编译 → 视频模型
       ↓
可选抽帧检查；独立 TTS；字幕与视频合成
```

这是一份逻辑关系图，不代表所有环节都串行，也不代表每次都执行所有分支。参考生视频跳过分镜图，广告有独立流程。

## 2. 提示词清单

分类说明：**创作指令**决定模型如何写；**执行提示词**实际发给媒体模型；**上下文/结构**影响模型理解但不是另一段故事；**流程指令**决定调用什么工具；**条件环节**只在对应模式、开关或操作下生效。

### A. Agent 与上下文

| 编号 | 环节、类型 | 来源与输入 | 输出、影响与适用范围 |
| --- | --- | --- | --- |
| P01 | 当前 Codex Agent 总指令（流程＋创作约束） | `server/agent_runtime/codex_instructions.py::build_agent_instructions`；模式、生成路线、语言、记忆；`codex_client.py` 装配 | 决定工具调用、草稿修复、授权理解、旁白交付、进度表达；当前默认后端的主要入口，不直接替代服务端剧本模板 |
| P02 | Claude Agent / 旧 Skill 与子 Agent（另一后端） | `options_assembler.py::_PERSONA_PROMPT`、`agent_runtime_profile/CLAUDE.{mode}.md`、`.claude/agents/`、`.claude/skills/` | Claude 路径的角色和操作指引。当前项目缺少旧 agents/skills 投影；本轮不判断为需要恢复的错误，也不能把它们当作当前 Codex 主入口 |
| P03 | 工具说明、参数解释、返回的下一步提示（接口上下文） | `server/agent_runtime/sdk_tools/`、`codex_support_tools.py`、`workflow_plan.py` 等 | Agent 据此选择工具、传参和处理错误。返回的源文/项目数据仍是数据；工具说明或错误提示可能改变后续编辑行为 |
| P04 | 用户要求、项目数据、记忆（动态上下文） | 用户消息、工具读取的原文/剧本、`project.json` 的风格/概述/配置、附加 `instructions`、记忆读取 | 不是单一固定模板；必须连同模板一起审阅，特别是同一要求在聊天、项目设置和附加指令间是否冲突。不应盲目向所有生成步骤塞入全部历史 |

### B. 从原文到正式剧本

| 编号 | 环节、类型 | 来源与输入 | 输出、影响与适用范围 |
| --- | --- | --- | --- |
| P05 | 参考图画风分析（视觉理解） | `lib/text_backends/prompts.py::STYLE_ANALYSIS_PROMPT`；上传的画风参考图；入口 `server/routers/files.py` | 提炼光线、色彩、媒介、质感等；要求不复述人物/物件。创建或调整画风时触发；预设画风另来自 `lib/style_templates.py`，不需要分析模型 |
| P06 | 项目概述生成（创作/提取） | `prompt_builders_script.py::build_overview_prompt`；原文、源文件性质、语言；`project_manager.py` 调用 | synopsis、题材、主题、世界观等，供后续理解故事。小说与成品剧本分支不同 |
| P07 | 角色、场景、道具与声音风格分析（Agent 创作） | 当前 P01 的 `analyze_assets` 指引＋源文＋已有资产；旧后端对应 `agents/analyze-assets.md` | Agent 提取/提出资产描述，调用正式工具写入。角色身份、外形、衍生外观与 voice_style 都可能影响后续；不是 `build_character_prompt` 在分析小说 |
| P08 | 分集规划（创作/提取） | `lib/episode_planner.py`；原文窗口、已规划上下文、创作类型、用户附加要求、目标体量 | 分集边界、标题、剧情节点、集尾钩子、下集预告；决定每集覆盖什么。ad 不走此路径 |
| P09 | 剧情演绎脚本规划（核心内容创作） | `prompt_builders_script.py::build_normalize_prompt`；本集原文、概述、资产、风格、当前/下集大纲、时长档位、语速估算、源类型、附加要求；调用 `server/text_generation.py::generate_drama_script_plan` | 定分镜顺序、对白/旁白 utterances、说话人、source_text、视觉描述、连续性与时长。小说可改编，成品剧本强调逐字保留；“叙事支离破碎”应首先在这里核对 |
| P10 | 旁白/解说脚本规划（内容拆分） | `build_narration_split_prompt`；原文、时长/语速/资产/大纲等 | narration 的 segments 与逐字 novel_text；当前 drama 项目不走此分支，不能把其规则直接套到剧情演绎 |
| P11 | 参考生视频单元规划（内容拆分） | `prompt_builders_reference.py::build_reference_units_split_prompt` | 规划视频单元的内容、发声、时长和核心资产；reference_video 路线专用，当前项目未选该路线 |
| P12 | 广告/短片脚本生成（独立创作分支） | `prompt_builders_ad.py::build_ad_prompt`、`build_ad_reference_prompt`；brief、目标时长、商品/资产、风格 | 单条短片的 shots 或 video_units；不走小说多集规划。没有商品也可以是一般创意短片 |
| P13 | 正式剧本的视觉提示词编写（创作） | `build_drama_prompt`、`build_narration_prompt`、`build_reference_video_prompt`；已确认的 script_plan＋项目视觉背景＋相关邻镜上下文；`lib/script_generator.py` 调用 | 生成 image_prompt/video_prompt 或参考视频书写正文。drama 的已确认对白、旁白不由该步骤重写。静态画面和动态动作的写作指南在本层，包含目前的“低缓”倾向 |
| P14 | 节奏、连续性及分集上下文（共用规则片段） | `agent_runtime_profile/.claude/references/episode-pacing-{drama,narration}.md`，经 `lib/prompt_rules/episode_pacing.py` 读取；`lib/shot_continuity.py`；reference 路线的 `reference-video-scene-rules.md` | 被拼入规划/视觉生成，而非独立的一次模型调用；控制节奏倾向、时空衔接、起止状态等。修改可能同时影响多种入口，需标明调用方 |

### C. 图片生成与编辑

| 编号 | 环节、类型 | 来源与输入 | 输出、影响与适用范围 |
| --- | --- | --- | --- |
| P15 | 角色资产图（执行） | `prompt_builders.py::build_character_prompt`；角色描述＋画风＋固定三视图版式/一致性/排除项 | 标准角色参考图。三视图规则不是视频分镜的排版规则 |
| P16 | 角色衍生外观（图片编辑执行） | `build_character_derivative_prompt`、`derivative_sheet_tasks.py`；本体图＋相对变化描述＋保留其余外观的约束 | 换装、年龄/形态等衍生参考图；不另行叠加项目画风，原图承载风格 |
| P17 | 场景资产图（执行） | `build_scene_prompt`；环境描述＋画风＋全景建立镜头/无出镜人物等约束 | 地点与空间参考图；这里对人物的排除不能照搬到有人物的分镜图 |
| P18 | 道具资产图（执行） | `build_prop_prompt`；物件描述＋画风＋浅灰背景等约束 | 道具参考图 |
| P19 | 商品资产图与商品保真（执行） | `build_product_prompt`、`append_product_fidelity_tail`；商品描述/原图及名称 | 商品标准图、分镜里的商品还原约束；是否追加取决于实际参考输入。当前剧集未使用商品路线 |
| P20 | 单张分镜首帧（执行） | `render_storyboard_image_prompt` / `build_storyboard_prompt`；P13 的 image_prompt＋项目 style/style_description＋排除项；相邻参考说明见 `storyboard_sequence.py` | 每镜一张静态首帧；图片比例/尺寸还有请求参数，不能靠提示词代替。上传现成图不调用此 prompt |
| P21 | 多宫格联合图（执行） | `lib/grid/prompt_builder.py::build_grid_prompt`；当前批次分镜、行列、单格比例、整体比例、占位、风格、连续性上下文及图片参考 | 等格联合图。当前是无边框/无间隙规则。行列和比例先由布局代码计算再写入 prompt；切分、尺寸检查本身不是 LLM。当前项目已使用此路线 |
| P22 | 图片局部编辑、首/尾帧修改（执行） | `server/services/image_edit_tasks.py`；当前图片＋用户或 Agent 编辑指令 | 图像的新版本；这条路径只把编辑指令作为 prompt，不拼原 image_prompt/项目风格。尾帧作为视频条件输入本身不是另一段自动生成的文本 prompt |

### D. 视频、声音与生成后检查

| 编号 | 环节、类型 | 来源与输入 | 输出、影响与适用范围 |
| --- | --- | --- | --- |
| P23 | 分镜视频请求拼装（执行） | `lib/prompt_utils.py::render_storyboard_video_prompt`；动作、镜头运动、环境音＋drama utterances 中的对白＋符合条件的 voice_style；`prompt_builders.py` 追加排除项 | 发给视频后端的文本。旁白不应被混作人物口型台词；当前 character_voice_binding=prompt 是文字音色约束，不是参考音频锁声 |
| P24 | 看首尾帧规划动作（条件视觉模型调用） | `server/services/motion_planning.py::MOTION_INSTRUCTIONS`；真实首/尾图、原动作、邻镜、时长、连续性 | 输出中间动作、机位、问题与检查点；通过后可替换执行时的 action/camera_motion。启用需 H3、任务 ID、尾帧或 continuity，并开启 `ARCREEL_STRICT_MOTION_REVIEW`。本轮 `.env` 未设置该开关，未读取运行中服务的进程环境，不能称它每次都执行 |
| P25 | AutoDL H3 提示词编译（执行适配） | 工作区 `media-worker/h3_prompt.py::format_h3_prompt`；P23 输出（可能先经 P24）及可选尾帧时间 | 编译 H3 的画面/声音字段、说话人标识、中文对白标记、首尾条件、无旁白/无 BGM 等声明。不是再次调用 LLM 改写；仍会加入影响生成行为的固定文字。需把上游文本和编译结果一起审核 |
| P26 | 参考生视频最终提示词（执行适配） | `lib/reference_video/prompt_render.py`；正文中的资产/台词标记、引用图片、可用参考音频、画风 | 资产图编号、人物/音色声明、对白、质量/稳定性/字幕等约束；与 storyboard 视频拼装是不同路径，当前项目未选 |
| P27 | 生成后抽帧动作检查（条件视觉模型调用） | `motion_planning.py::review_task_motion`；参考图、已有动作计划、最多 12 张按时间抽取的视频帧 | 输出动作/空间的抽样检查及不确定性。只在有相应计划记录等条件下执行；不是全帧检查，更不是语音识别、口型或整集叙事验收 |
| P28 | 独立旁白 TTS（声音条件输入） | 脚本发声正文 → 旁白任务 → `media-worker/cloud_gateway.py` → `cloud_tts.py`；正文 input、音色 reference 音频及 transcript、speed | 生成独立声音。CosyVoice zero-shot 使用参考声音与对应文字作为条件；这不等于“请写得更感人”的故事创作 prompt。当前对白主要来自视频原生音轨，不能认为这一路已统一接管所有角色对白 |

### E. 容易遗漏或误判的外围层

| 编号 | 环节、类型 | 来源与输入 | 输出、影响与适用范围 |
| --- | --- | --- | --- |
| P29 | 输出结构和字段解释（结构化上下文） | `lib/script_models.py`、各请求 response_schema、motion/schema 模型、工具参数 schema | 字段描述、枚举、约束会影响模型输出；部分走 schema/tool 通道，并非全部是可见字符串 prompt。优化正文时要检查有没有与字段定义冲突 |
| P30 | 后端与兼容性包装（执行/格式） | `lib/text_backends/`、`image_backends/`、`video_backends/`、自定义端点定义 | 可加入 JSON 输出说明、图片引用标签、比例文字或执行必要字段。比如 Codex 图片后端追加 Requested aspect ratio；Gemini 组装带标签参考图；文本兼容层可能注入 JSON 指令。不是统一的故事创作模板，按实际供应商逐项追踪 |
| P31 | 草稿修复、报错后定点编辑（Agent 创作/操作） | 当前 P01 中 `_DRAFTS`/修复流程＋草稿正文＋violations＋用户修改意见；旧后端也有相应指引 | Agent 可直接改内容，即使 P09/P13 模板没变也能造成差异。需记录修复前后对白、旁白、顺序、时长变更；不能漏掉这一路 |
| P32 | 合成操作指引与临时审计脚本（流程/辅助） | 当前 Codex 总指令的交付限制；旧 `compose-video/SKILL.md`；工作区临时脚本如 `audit_episode_continuity.py` | 旧合成 Skill 约束 Agent 操作，实际 FFmpeg 不是创作模型。临时审计脚本有自己的检查 prompt，但不是自动生产必经步骤；不能只改脚本就声称应用升级 |

## 3. 当前已发现、后续值得逐字审阅的原有规则

以下仅摘录现状，不构成改动决定：

1. **P13 动作写作**：`prompt_builders_script.py` 含“优先低缓、连贯的细微动作”“更长的分镜保持单一低缓的动作主线”。与当前慢动作反馈有关。
2. **P09 发声归属**：同一生成分镜不能混合 dialogue 与 voiceover。需检查其拆分是否保留完整叙事；不应仅删除限制而忽略后端能力。
3. **P14 节奏规则**：目前有开篇约 4 秒钩子、中段约 15 秒情绪转折等建议。应核对实际全文与应用效果，不把数值当作所有故事通用的质量指标。
4. **P21 宫格布局**：当前要求所有格等大、无边框/无间隙，并设置占位。需要连同真实行列参数、图片输出比例和裁切结果审阅，不能只改“3×3”几个字。
5. **P23/P25 声音**：对白、人物声音风格、无对白镜头闭嘴、环境音和无 BGM 等规则跨两层组装。台词的来源是正式 utterances，不能在动作 prompt 里另造一套台词。
6. **P01/P02 运行方式**：当前默认 Codex 指令与仓库旧 Skill 是两条不同路径；优化前先确认目标会话使用哪一条。此次已更正“只需改旧 Agent Skill”的可能误解。

## 4. 哪些问题不能只改 prompt

- 支持时长、生成尺寸、24fps、采样步数、队列和计费属于模型/系统参数。
- 宫格模板选择、等比分割、落格审核属于布局与图像处理。
- 发声准入、revision、素材时效、确认记录属于程序校验。
- 当前 presentation 的旁白起点/边界、机械字幕分配、整集硬拼接、音量混合、末帧停留属于声音与剪辑实现。
- 当前第二集 `media-worker/export_episode2.py` 的分句字幕、短暂末帧停留是本地导出实现，不是 Agent prompt，更不能因此认定应用的时间线功能已经完成。

这些约束会被写进 prompt 或作为输入影响创作，但改变说明文字不会自动改变代码行为。

## 5. 源码入口（点击可打开）

- [当前 Codex Agent 指令](<D:/projects/SELF/zero evo/ArcReel/server/agent_runtime/codex_instructions.py>)
- [Agent 实际装配与工具](<D:/projects/SELF/zero evo/ArcReel/server/agent_runtime/codex_client.py>)
- [旧 Claude Agent 装配](<D:/projects/SELF/zero evo/ArcReel/server/agent_runtime/options_assembler.py>)
- [剧情、旁白规划与视觉写作模板](<D:/projects/SELF/zero evo/ArcReel/lib/prompt_builders_script.py>)
- [分集规划](<D:/projects/SELF/zero evo/ArcReel/lib/episode_planner.py>)
- [参考生视频规划与书写](<D:/projects/SELF/zero evo/ArcReel/lib/prompt_builders_reference.py>)
- [广告/短片模板](<D:/projects/SELF/zero evo/ArcReel/lib/prompt_builders_ad.py>)
- [共用剧情节奏原文](<D:/projects/SELF/zero evo/ArcReel/agent_runtime_profile/.claude/references/episode-pacing-drama.md>)
- [连续性规则与开关](<D:/projects/SELF/zero evo/ArcReel/lib/shot_continuity.py>)
- [资产图、分镜图、负面约束](<D:/projects/SELF/zero evo/ArcReel/lib/prompt_builders.py>)
- [宫格提示词](<D:/projects/SELF/zero evo/ArcReel/lib/grid/prompt_builder.py>)
- [视频文本、对白及声音风格拼装](<D:/projects/SELF/zero evo/ArcReel/lib/prompt_utils.py>)
- [看图动作规划及抽帧检查](<D:/projects/SELF/zero evo/ArcReel/server/services/motion_planning.py>)
- [H3 提示词编译](<D:/projects/SELF/zero evo/media-worker/h3_prompt.py>)
- [CosyVoice 条件输入](<D:/projects/SELF/zero evo/media-worker/cloud_tts.py>)
- [最终图/视频文本预览服务](<D:/projects/SELF/zero evo/ArcReel/server/services/prompt_preview.py>)

## 6. 后续逐项审阅方式

每次只选一个编号，提供以下材料：

1. **当前原文**：完整规则、字段说明及调用条件，不先换成我的改写版本。
2. **实际输入与最终输出文本**：从同一项目、同一段剧情取样，区分模板、项目数据、最终请求；不要暴露凭证。
3. **问题证据**：具体句子、镜头或生成结果，以及可能的影响；尚未验证的明确列为推测。
4. **拟改差异**：只展示这一个环节的调整及影响范围，交用户讨论确定。
5. **验证与回滚**：先只生成文本或只读预览，再由用户决定是否小样生成；保留旧版本，满意后再扩大范围。

系统已有部分 dry_run（脚本规划、视觉提示词编写）及图/视频 prompt 预览能力，可以优先利用。预览服务展示的是应用层拼装，不代表一定包括随后启用的视觉动作规划替换、H3 云端编译或供应商内部处理；比较时应标清层级。调用前仍要检查该路径是否有状态写入，不能只凭名称认定绝对无副作用。

本清单只建议首先审阅 P09（内容规划）与 P13（视觉/动作编写），不自动开始优化。具体顺序和每项改动由用户决定。

## 7. 核对边界

- 本次检查的是当前本地源码、项目文件及数据库默认 Agent 选择；未重新连接 AutoDL，不声称远端运行中的文件已逐字比对。
- 工作区 `media-worker` 的 `h3_prompt.py`、`cloud_gateway.py`、`cloud_tts.py` 与仓库 `deploy/media-worker` 归档逐字相同。未来修改需核对操作目录、归档及远端部署三处，不能只改其中一份就宣布生效。
- H3 workflow JSON 内的示例 prompt 有“slow camera movement”，但网关会用请求正文覆盖该字段；不能把模板里的这个占位文本直接判成实际视频慢动作的原因。
- 静态代码存在某个 prompt 不证明某个已生成视频用过它。正式逐项优化前，应匹配实际任务记录/请求快照，固定输入与规则版本再比较。

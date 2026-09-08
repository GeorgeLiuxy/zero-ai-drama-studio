MESSAGES = {
    "codex_agent_wrong_session": "工具调用不属于当前项目会话，未执行操作。",
    "codex_agent_stale_turn": "工具调用来自已结束的对话轮次，未执行操作。",
    "codex_agent_call_id_conflict": "同一调用编号提交了不同参数，未重复执行操作。",
    "codex_agent_invalid_tool_arguments": "工具参数不符合要求，请根据返回的字段和规则修正后重试。",
    "codex_agent_unknown_tool": "当前项目没有此工具，请选择已提供的业务工具。",
    "script_plan_saved_review_blocked": "已保存不代表可确认：以下分镜仍受发声准入阻塞，修复后再进行内容审核确认。",
    "script_plan_saved_review_pending": "已保存，发声准入检查通过；仍须按当前授权完成内容审核确认后才能生成视觉内容。",
    "script_plan_speech_repair_hint": "保留真实发声内容与顺序：混合旁白与角色台词须拆分镜头；动作与屏幕字放入画面描述，不得删改 needs_replan 绕过检查。",
    "system_start": "正在启动 ArcReel 系统...",
    "worker_start": "正在启动 GenerationWorker...",
}

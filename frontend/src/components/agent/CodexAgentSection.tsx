import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { AgentRuntimeConfig, AgentRuntimePatch } from "@/types";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { useWarnUnsaved } from "@/hooks/useWarnUnsaved";
import { CredentialsSection } from "./CredentialsSection";
import { SectionShell } from "@/components/ui/SectionShell";
import { FieldLabel } from "@/components/ui/FieldLabel";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS, INPUT_CLS } from "@/components/ui/darkroom-tokens";

interface Draft {
  enabled: boolean;
  model: string;
  effort: string;
}

function draftFrom(config: AgentRuntimeConfig): Draft {
  return { enabled: config.enabled, model: config.model ?? "", effort: config.reasoning_effort };
}

export function CodexAgentSection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [config, setConfig] = useState<AgentRuntimeConfig | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const dirty = !!config && !!draft && (
    draft.enabled !== config.enabled || draft.model !== (config.model ?? "") || draft.effort !== config.reasoning_effort
  );
  useWarnUnsaved(dirty);

  const load = useCallback(() => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    return API.getAgentRuntime({ signal: controller.signal }).then((result) => {
      if (controller.signal.aborted) return;
      setConfig(result);
      setDraft((previous) => previous ?? draftFrom(result));
      setFailed(false);
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
  }, []);

  useEffect(() => {
    void load();
    return () => requestRef.current?.abort();
  }, [load]);

  const save = async () => {
    if (!draft || !config || !dirty) return;
    const controller = requestRef.current;
    setBusy(true);
    setFailed(false);
    const patch: AgentRuntimePatch = {};
    if (draft.enabled !== config.enabled) patch.enabled = draft.enabled;
    if (draft.model && draft.model !== config.model) patch.model = draft.model;
    if (draft.effort !== config.reasoning_effort) patch.reasoning_effort = draft.effort;
    try {
      const result = await API.updateAgentRuntime(patch);
      if (!controller?.signal.aborted) {
        setConfig(result);
        setDraft(draftFrom(result));
      }
      void useConfigStatusStore.getState().refresh();
    } catch {
      if (!controller?.signal.aborted) setFailed(true);
    } finally {
      if (!controller?.signal.aborted) setBusy(false);
    }
  };

  const refresh = async () => {
    const controller = requestRef.current;
    setBusy(true);
    setFailed(false);
    try {
      const connection = await API.setCodexConnection(true);
      if (!controller?.signal.aborted) {
        await load();
        if (!requestRef.current?.signal.aborted && !connection.connected) setFailed(true);
      }
      void useConfigStatusStore.getState().refresh();
    } catch {
      if (!controller?.signal.aborted) setFailed(true);
    } finally {
      if (!requestRef.current?.signal.aborted) setBusy(false);
    }
  };

  const selected = config?.models.find((model) => model.id === draft?.model);
  const efforts = selected?.supported_reasoning_efforts ?? [];

  return (
    <>
      <SectionShell kicker="Codex Agent" title={t("agent_codex_title")}>
        <div className="space-y-4 text-[12.5px]">
          <p className="text-text-3">{t("agent_codex_description")}</p>
          {(failed || (config?.enabled && config.error_code)) && (
            <p role="alert" className="text-warm">{t("agent_codex_error")}</p>
          )}
          {!config || !draft ? (
            failed ? (
              <button type="button" className={GHOST_BTN_CLS} onClick={() => void load()}>{t("common:retry")}</button>
            ) : <p role="status">{t("common:loading")}</p>
          ) : (
            <>
              <label className="flex items-center gap-2 text-text">
                <input type="checkbox" checked={draft.enabled} disabled={busy} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />
                {t("agent_codex_enable")}
              </label>
              <p role="status" className="text-text-2">
                {t(config.enabled ? (config.ready ? "agent_codex_ready" : "agent_codex_not_ready") : "agent_codex_legacy")}
              </p>
              <p className="text-text-3">{t("codex_login_instruction")} <code className="font-mono">codex login</code></p>
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <FieldLabel htmlFor="codex-agent-model">{t("agent_codex_model")}</FieldLabel>
                  <select id="codex-agent-model" className={`${INPUT_CLS} mt-1.5`} value={draft.model} disabled={busy || !draft.enabled} onChange={(event) => {
                    const model = config.models.find((item) => item.id === event.target.value);
                    const supported = model?.supported_reasoning_efforts ?? [];
                    const effort = supported.includes(draft.effort) ? draft.effort : supported.includes("low") ? "low" : supported[0] ?? draft.effort;
                    setDraft({ ...draft, model: event.target.value, effort });
                  }}>
                    {!selected && <option value={draft.model}>{draft.model || t("agent_codex_select_model")}</option>}
                    {config.models.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}
                  </select>
                </div>
                <div>
                  <FieldLabel htmlFor="codex-agent-effort">{t("agent_codex_effort")}</FieldLabel>
                  <select id="codex-agent-effort" className={`${INPUT_CLS} mt-1.5`} value={draft.effort} disabled={busy || !draft.enabled || efforts.length === 0} onChange={(event) => setDraft({ ...draft, effort: event.target.value })}>
                    {!efforts.includes(draft.effort) && <option value={draft.effort}>{draft.effort}</option>}
                    {efforts.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
                  </select>
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={ACCENT_BTN_CLS} style={ACCENT_BUTTON_STYLE} disabled={busy || !dirty || (draft.enabled && !selected)} onClick={() => void save()}>{t("agent_codex_save")}</button>
                <button type="button" className={GHOST_BTN_CLS} disabled={busy} onClick={() => void refresh()}>{t("codex_refresh")}</button>
              </div>
              <p className="text-text-3">{t("agent_codex_session_note")}</p>
            </>
          )}
        </div>
      </SectionShell>
      {config && !config.enabled && <CredentialsSection />}
    </>
  );
}

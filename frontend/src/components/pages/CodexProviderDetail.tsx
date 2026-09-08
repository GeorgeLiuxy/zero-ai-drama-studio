import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import type { CodexConnectionStatus } from "@/types";
import { ProviderIcon } from "@/components/ui/ProviderIcon";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

export function CodexProviderDetail({ onSaved }: { onSaved?: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [connection, setConnection] = useState<CodexConnectionStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const requestRef = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    return API.getCodexConnection({ signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) {
        setConnection(result);
        setFailed(false);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setFailed(true);
    });
  }, []);

  useEffect(() => {
    void load();
    return () => requestRef.current?.abort();
  }, [load]);

  const changeConnection = async (enabled: boolean) => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    setBusy(true);
    setFailed(false);
    try {
      const result = await API.setCodexConnection(enabled);
      if (!controller.signal.aborted) setConnection(result);
      onSaved?.();
    } catch {
      if (!controller.signal.aborted) setFailed(true);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  };

  return (
    <div className="max-w-2xl space-y-6">
      <div className="flex items-start gap-3">
        <ProviderIcon providerId="codex" className="mt-0.5 h-7 w-7 shrink-0" />
        <div>
          <h3 className="font-editorial text-[22px] leading-tight text-text">{t("codex_title")}</h3>
          <p className="mt-1.5 text-[12.5px] leading-relaxed text-text-3">{t("codex_description")}</p>
        </div>
      </div>

      <div className="space-y-3 rounded-lg border border-hairline-soft bg-bg-grad-a/35 p-4 text-[12.5px]">
        <p className="text-text-2">{t("codex_login_instruction")}</p>
        <code className="inline-block rounded border border-hairline-soft px-2 py-1 font-mono text-text">codex login</code>
        <p className="text-text-3">{t("codex_subscription_note")}</p>
      </div>

      {(failed || connection?.error_code) && (
        <p role="alert" className="text-[12.5px] text-warm">{t("codex_connection_error")}</p>
      )}

      {!connection ? (
        failed ? (
          <button type="button" onClick={() => { setFailed(false); void load(); }} className={GHOST_BTN_CLS}>{t("common:retry")}</button>
        ) : (
          <p role="status" className="flex items-center gap-2 text-[12.5px] text-text-3">
            <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
            {t("common:loading")}
          </p>
        )
      ) : (
        <>
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-3 text-[12.5px]">
            <dt className="text-text-3">{t("codex_connection_label")}</dt>
            <dd className="text-text" aria-live="polite">{t(connection.connected ? "codex_connected" : "codex_disconnected")}</dd>
            {connection.plan_type && (
              <>
                <dt className="text-text-3">{t("codex_plan_label")}</dt>
                <dd className="text-text">{connection.plan_type}</dd>
              </>
            )}
            <dt className="text-text-3">{t("codex_models_label")}</dt>
            <dd className="text-text">{connection.models_count}</dd>
            <dt className="text-text-3">{t("codex_image_label")}</dt>
            <dd className="text-text">{t(connection.image_generation ? "codex_image_available" : "codex_image_unavailable")}</dd>
          </dl>
          <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => void changeConnection(true)} className={ACCENT_BTN_CLS} style={ACCENT_BUTTON_STYLE}>
              {busy && <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />}
              {t(connection.enabled ? "codex_refresh" : "codex_connect")}
            </button>
            {connection.enabled && (
              <button type="button" disabled={busy} onClick={() => void changeConnection(false)} className={GHOST_BTN_CLS}>
                {t("codex_disconnect")}
              </button>
            )}
          </div>
          <p className="text-[12px] text-text-3">{t("codex_disconnect_note")}</p>
        </>
      )}
    </div>
  );
}

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import type { CodexConnectionStatus } from "@/types";
import { CodexProviderDetail } from "./CodexProviderDetail";

const DISCONNECTED: CodexConnectionStatus = {
  enabled: false,
  connected: false,
  plan_type: null,
  models_count: 0,
  image_generation: false,
  error_code: null,
};

describe("CodexProviderDetail", () => {
  beforeEach(async () => {
    await act(async () => i18n.changeLanguage("zh"));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("connects, refreshes and disconnects the subscription through the connection API", async () => {
    const requests: { url: string; body?: string }[] = [];
    let enabledChecks = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, body: init?.body as string | undefined });
      let result = DISCONNECTED;
      if (init?.body === JSON.stringify({ enabled: true })) {
        enabledChecks += 1;
        result = {
          ...DISCONNECTED,
          enabled: true,
          connected: true,
          plan_type: "Pro",
          models_count: enabledChecks + 2,
          image_generation: enabledChecks > 1,
        };
      }
      return Response.json(result);
    }));

    render(<CodexProviderDetail />);
    await screen.findByText("未连接");
    expect(screen.getByText("codex login")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "连接订阅" }));
    await screen.findByText("已连接");
    expect(screen.getByText("Pro")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("尚未确认可用")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新连接" }));
    await screen.findByText("4");
    expect(screen.getByText("可用")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "断开连接" }));
    await screen.findByText("未连接");
    expect(screen.queryByRole("button", { name: "断开连接" })).not.toBeInTheDocument();
    expect(screen.getByText(/不会退出共享的 Codex 账号/)).toBeInTheDocument();
    expect(requests).toEqual([
      { url: "/api/v1/providers/codex/connection", body: undefined },
      { url: "/api/v1/providers/codex/connection", body: '{"enabled":true}' },
      { url: "/api/v1/providers/codex/connection", body: '{"enabled":true}' },
      { url: "/api/v1/providers/codex/connection", body: '{"enabled":false}' },
    ]);
  });

  it("shows a localized recovery message for a failed account probe without exposing its error code", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => Response.json(
      init?.method === "POST"
        ? { ...DISCONNECTED, enabled: true, error_code: "codex_internal_auth_error" }
        : DISCONNECTED,
    )));

    render(<CodexProviderDetail />);
    fireEvent.click(await screen.findByRole("button", { name: "连接订阅" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请确认服务端已安装 Codex CLI");
    expect(screen.queryByText("codex_internal_auth_error")).not.toBeInTheDocument();
    expect(screen.getByText("未连接")).toBeInTheDocument();
    await act(async () => i18n.changeLanguage("en"));
    expect(screen.getByRole("alert")).toHaveTextContent("Check that the Codex CLI is installed");
    expect(screen.getByRole("button", { name: "Refresh connection" })).toBeEnabled();
  });

  it("allows retrying a failed status read without enabling the connection", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new Error("internal transport detail"))
      .mockResolvedValueOnce(Response.json(DISCONNECTED));
    vi.stubGlobal("fetch", fetchMock);

    render(<CodexProviderDetail />);
    expect(await screen.findByRole("alert")).not.toHaveTextContent("internal transport detail");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("未连接");
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "连接订阅" })).toBeEnabled();
  });
});

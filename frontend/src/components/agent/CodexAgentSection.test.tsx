import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import type { AgentRuntimeConfig } from "@/types";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { CodexAgentSection } from "./CodexAgentSection";

function runtimeConfig(): AgentRuntimeConfig {
  return {
    enabled: true,
    runtime: "codex",
    ready: true,
    model: "model-small",
    reasoning_effort: "low",
    models: [
      { id: "model-small", display_name: "Small", supported_reasoning_efforts: ["low", "high"] },
      { id: "model-large", display_name: "Large", supported_reasoning_efforts: ["medium", "xhigh"] },
    ],
    error_code: null,
  };
}

describe("CodexAgentSection", () => {
  beforeEach(async () => {
    await act(async () => i18n.changeLanguage("zh"));
    vi.spyOn(useConfigStatusStore.getState(), "refresh").mockResolvedValue();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("saves the selected model and supported effort, and only exposes legacy credentials after disabling Codex", async () => {
    let config = runtimeConfig();
    const patches: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/agent/credentials") return Response.json({ credentials: [] });
      if (url === "/api/v1/agent/preset-providers") return Response.json({ providers: [], custom_sentinel_id: "__custom__" });
      if (url !== "/api/v1/agent/runtime") throw new Error(`Unexpected request: ${url}`);
      if (init?.method === "PATCH") {
        const patch = JSON.parse(init.body as string) as Partial<AgentRuntimeConfig>;
        patches.push(patch);
        config = { ...config, ...patch };
        config.runtime = config.enabled ? "codex" : "claude";
      }
      return Response.json(config);
    }));

    render(<CodexAgentSection />);
    expect(await screen.findByText("Codex Agent 已就绪")).toBeInTheDocument();
    expect(screen.queryByTestId("credential-list-empty")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("智能体模型"), { target: { value: "model-large" } });
    expect(screen.getByLabelText("推理强度")).toHaveValue("medium");
    expect(screen.queryByRole("option", { name: "low" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("推理强度"), { target: { value: "xhigh" } });
    fireEvent.click(screen.getByRole("button", { name: "保存智能体设置" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存智能体设置" })).toBeDisabled());
    expect(patches).toEqual([{ model: "model-large", reasoning_effort: "xhigh" }]);

    fireEvent.click(screen.getByRole("checkbox", { name: "内嵌智能体使用 Codex" }));
    fireEvent.click(screen.getByRole("button", { name: "保存智能体设置" }));
    expect(await screen.findByText("内嵌智能体使用下方兼容凭证")).toBeInTheDocument();
    expect(await screen.findByTestId("credential-list-empty")).toBeInTheDocument();
    expect(screen.getByLabelText("智能体模型")).toBeDisabled();
    expect(patches[1]).toEqual({ enabled: false });

    fireEvent.click(screen.getByRole("checkbox", { name: "内嵌智能体使用 Codex" }));
    fireEvent.click(screen.getByRole("button", { name: "保存智能体设置" }));
    await screen.findByText("Codex Agent 已就绪");
    expect(screen.queryByTestId("credential-list-empty")).not.toBeInTheDocument();
    expect(patches[2]).toEqual({ enabled: true });
  });

  it("refreshes the subscription without dropping an unsaved model selection", async () => {
    let config = { ...runtimeConfig(), ready: false };
    const connectionRequests: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/providers/codex/connection" && init?.method === "POST") {
        connectionRequests.push(JSON.parse(init.body as string));
        config = { ...config, ready: true };
        return Response.json({ connected: true });
      }
      if (url === "/api/v1/agent/runtime") return Response.json(config);
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<CodexAgentSection />);
    await screen.findByText("Codex Agent 需要连接订阅");
    fireEvent.change(screen.getByLabelText("智能体模型"), { target: { value: "model-large" } });
    fireEvent.click(screen.getByRole("button", { name: "刷新连接" }));
    await screen.findByText("Codex Agent 已就绪");
    expect(screen.getByLabelText("智能体模型")).toHaveValue("model-large");
    expect(screen.getByRole("button", { name: "保存智能体设置" })).toBeEnabled();
    expect(connectionRequests).toEqual([{ enabled: true }]);
  });

  it("retains the draft and translates a failed save without exposing transport details", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PATCH"
        ? Response.json({ detail: "private runtime detail" }, { status: 500 })
        : Response.json(runtimeConfig()),
    ));

    render(<CodexAgentSection />);
    await screen.findByText("Codex Agent 已就绪");
    fireEvent.change(screen.getByLabelText("推理强度"), { target: { value: "high" } });
    fireEvent.click(screen.getByRole("button", { name: "保存智能体设置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("请检查 Codex 登录状态和所选模型");
    expect(screen.queryByText("private runtime detail")).not.toBeInTheDocument();
    expect(screen.getByLabelText("推理强度")).toHaveValue("high");
    await act(async () => i18n.changeLanguage("en"));
    expect(screen.getByRole("alert")).toHaveTextContent("Check the Codex login");
  });
});

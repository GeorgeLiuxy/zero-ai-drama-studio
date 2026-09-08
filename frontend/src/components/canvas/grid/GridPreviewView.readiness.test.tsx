import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { NarrationSegment } from "@/types";
import type { GridCapability, GridGeneration, GridSplitPreview } from "@/types/grid";
import { GridPreviewView } from "./GridPreviewView";

const capability: GridCapability = {
  large_grid_allowed: false, max_cell_count: 9, video_aspect_ratio: "9:16",
  layouts: [
    { scene_count: 9, grid_size: "3x3", rows: 3, cols: 3, cell_count: 9, placeholder_count: 0, grid_aspect_ratio: "9:16" },
    { scene_count: 3, grid_size: "2x2", rows: 2, cols: 2, cell_count: 4, placeholder_count: 1, grid_aspect_ratio: "9:16" },
    { scene_count: 7, grid_size: "3x3", rows: 3, cols: 3, cell_count: 9, placeholder_count: 2, grid_aspect_ratio: "9:16" },
  ],
};

function segments(count: number): NarrationSegment[] {
  return Array.from({ length: count }, (_, i) => ({
    segment_id: `S${i + 1}`, episode: 1, duration_seconds: 5, segment_break: i === 12,
    novel_text: "", characters_in_segment: [], image_prompt: "", video_prompt: "", transition_to_next: "cut",
  })) as NarrationSegment[];
}

function grids(firstReady = true): GridGeneration[] {
  return [9, 3].map((count, index) => ({
    id: `grid-${index + 1}`, episode: 1, script_file: "episode_1.json",
    scene_ids: Array.from({ length: count }, (_, i) => `S${i + 1 + index * 9}`),
    grid_image_path: `grids/grid-${index + 1}.png`, rows: index === 0 ? 3 : 2, cols: index === 0 ? 3 : 2,
    cell_count: index === 0 ? 9 : 4, frame_chain: [], status: "completed", prompt: null,
    provider: "custom", model: "gpt-image-2", grid_size: index === 0 ? "3x3" : "2x2",
    created_at: `2026-09-08T07:00:0${index}Z`, error_message: null, split_at: null,
    video_aspect_ratio: "9:16",
    readiness: {
      image_exists: true, geometry_valid: index === 0,
      issues: index === 0 ? [] : ["grid_aspect_ratio_mismatch"],
      ready_scene_ids: index === 0 && firstReady ? Array.from({ length: 9 }, (_, i) => `S${i + 1}`) : [],
    },
  }));
}

function preview(grid: GridGeneration): GridSplitPreview {
  return {
    review_token: grid.id, valid: grid.readiness?.geometry_valid === true,
    issues: grid.readiness?.issues ?? [], width: grid.rows === 3 ? 1728 : 1024, height: grid.rows === 3 ? 3072 : 1536,
    cell_width: grid.rows === 3 ? 576 : 512, cell_height: grid.rows === 3 ? 1024 : 768, output_width: 576, output_height: 1024,
    video_aspect_ratio: "9:16",
    cells: Array.from({ length: grid.cell_count }, (_, index) => ({
      index, row: Math.floor(index / grid.cols), col: index % grid.cols,
      scene_id: grid.scene_ids[index] ?? null, placeholder: index >= grid.scene_ids.length,
      image: "data:image/png;base64,AA==",
    })),
  };
}

function view(count = 12, projectName = "demo") {
  return <GridPreviewView projectName={projectName} episode={1} scriptFile="episode_1.json" segments={segments(count)} contentMode="narration" />;
}

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useProjectsStore.setState({ currentProjectName: null, currentProjectData: null, assetFingerprints: {} });
  useTasksStore.setState({ tasks: [], optimisticActive: new Set(), optimisticActiveScriptFile: new Set() });
  vi.spyOn(API, "getGridCapability").mockResolvedValue(capability);
});

describe("多张联合图的正式首帧就绪与操作范围", () => {
  it.each([12, 19])("%i 个分镜仅 9 个正式首帧可用时不显示全部就绪，第二张 2×2 图在预览前标出不合格", async (count) => {
    const records = grids();
    vi.spyOn(API, "listGrids").mockResolvedValue(records);
    vi.spyOn(API, "getGrid").mockImplementation(async (_project, id) => records.find((grid) => grid.id === id)!);
    vi.spyOn(API, "getGridSplitPreview").mockResolvedValue(preview(records[1]));
    const split = vi.spyOn(API, "splitGrid");
    render(view(count));
    expect(await screen.findByText(new RegExp(`^首帧已就绪 9/${count} · 当前联合图生成完成 2/2`))).toBeInTheDocument();
    expect(screen.getByText("第 2 张：3 个分镜 · 2 行 × 2 列 · 1 个占位 · 联合图要求 9:16 · 每格首帧要求 9:16")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "第 2 张" }));
    expect(await screen.findByText(/当前第 2\/2 张 · 仅处理本图 3 个分镜 · 首帧已就绪 0\/3/)).toBeInTheDocument();
    expect(screen.getByText(/当前联合图不符合尺寸或分割要求/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览切分当前图" }));
    expect(await screen.findByRole("button", { name: "已逐格确认，写入首帧" })).toBeDisabled();
    expect(screen.getByRole("img", { name: "第 4 格 · 占位格（不写入分镜）" })).toBeInTheDocument();
    expect(split).not.toHaveBeenCalled();
    expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
  });

  it("只确认第一张的 9 格，并重拉列表和详情取得 9/12 就绪；不会替第二张落格", async () => {
    let records = grids(false);
    vi.spyOn(API, "listGrids").mockImplementation(async () => records);
    vi.spyOn(API, "getGrid").mockImplementation(async (_project, id) => records.find((grid) => grid.id === id)!);
    vi.spyOn(API, "getGridSplitPreview").mockImplementation(async (_project, id) => preview(records.find((grid) => grid.id === id)!));
    vi.spyOn(API, "splitGrid").mockImplementation(async () => {
      records = grids(true);
      return { success: true, split_at: "2026-09-08T08:00:00Z", updated_scene_ids: records[0].scene_ids, missing_scene_ids: [], asset_fingerprints: {} };
    });
    render(view());
    expect(await screen.findByText(/^首帧已就绪 0\/12/)).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "预览切分当前图" }));
    expect(await screen.findByRole("img", { name: "第 9 格 · S9" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /S10/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "已逐格确认，写入首帧" }));
    expect(await screen.findByText(/^首帧已就绪 9\/12/)).toBeInTheDocument();
    expect(screen.getByText(/当前第 1\/2 张.*首帧已就绪 9\/9/)).toBeInTheDocument();
    expect(API.splitGrid).toHaveBeenCalledExactlyOnceWith("demo", "grid-1", "grid-1");
    fireEvent.click(screen.getByRole("button", { name: "第 2 张" }));
    expect(await screen.findByText(/当前第 2\/2 张.*首帧已就绪 0\/3/)).toBeInTheDocument();
  });

  it("旧接口缺少 readiness 时不通过 completed 或 split_at 猜测首帧可用", async () => {
    const records = grids().map((grid) => ({ ...grid, readiness: undefined, split_at: "2026-09-08T08:00:00Z" }));
    vi.spyOn(API, "listGrids").mockResolvedValue(records);
    vi.spyOn(API, "getGrid").mockResolvedValue(records[0]);
    render(view());
    expect(await screen.findByText(/当前第 1\/2 张.*首帧已就绪 0\/9/)).toBeInTheDocument();
    expect(screen.getByText(/^首帧已就绪 0\/12/)).toBeInTheDocument();
    expect(screen.getByText("首帧待检查或确认")).toBeInTheDocument();
  });

  it("切换项目后晚到的旧列表不会覆盖当前项目就绪计数", async () => {
    let finishOld: (value: GridGeneration[]) => void = () => {};
    vi.spyOn(API, "listGrids").mockImplementation(async (project) => project === "old"
      ? new Promise((resolve) => { finishOld = resolve; }) : grids(false));
    vi.spyOn(API, "getGrid").mockResolvedValue(grids(false)[0]);
    const { rerender } = render(view(12, "old"));
    rerender(view(12, "new"));
    expect(await screen.findByText(/当前第 1\/2 张.*首帧已就绪 0\/9/)).toBeInTheDocument();
    await act(async () => { finishOld(grids()); });
    expect(screen.getByText(/^首帧已就绪 0\/12/)).toBeInTheDocument();
    expect(screen.queryByText(/^首帧已就绪 9\/12/)).not.toBeInTheDocument();
  });
});

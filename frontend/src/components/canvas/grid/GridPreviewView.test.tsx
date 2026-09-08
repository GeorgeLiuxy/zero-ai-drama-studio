import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useProjectsStore } from "@/stores/projects-store";
import type { NarrationSegment } from "@/types";
import { GridPreviewView } from "./GridPreviewView";

// 面板本身有独立测试，这里只关心批次预览的档位与张数，避免拉进它的数据加载。
vi.mock("@/components/canvas/timeline/GridPreviewPanel", () => ({
  GridPreviewPanel: () => <div data-testid="grid-panel" />,
}));

function makeSegments(count: number): NarrationSegment[] {
  return Array.from({ length: count }, (_, i) => ({
    segment_id: `SEG-${i + 1}`,
    episode: 1,
    duration_seconds: 5,
    segment_break: false,
    novel_text: "",
    characters_in_segment: [],
    image_prompt: "",
    video_prompt: "",
    transition_to_next: "cut",
  })) as NarrationSegment[];
}

function renderView(segments: NarrationSegment[]) {
  return render(
    <GridPreviewView
      projectName="demo"
      episode={1}
      scriptFile="episode_1.json"
      segments={segments}
      contentMode="narration"
    />,
  );
}

beforeEach(() => {
  vi.spyOn(API, "listGrids").mockResolvedValue([]);
  useProjectsStore.setState({ currentProjectName: null, currentProjectData: null });
});

describe("GridPreviewView 的动态布局预览", () => {
  it.each([
    ["9:16", 3, 4, "3:4"],
    ["16:9", 4, 3, "4:3"],
    ["4:3", 3, 4, "16:9"],
  ])("12 个 %s 首帧按后端布局展示行列与两种比例", async (ratio, rows, cols, canvasRatio) => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({
      large_grid_allowed: true, max_cell_count: 25, video_aspect_ratio: ratio,
      layouts: [{ scene_count: 12, grid_size: "grid_12", rows, cols, cell_count: 12, placeholder_count: 0, grid_aspect_ratio: canvasRatio }],
    });
    renderView(makeSegments(12));
    expect(await screen.findByText("组 #1 · 12 个分镜 · 生成方案：1 张联合图")).toBeInTheDocument();
    expect(screen.getByText(`第 1 张：12 个分镜 · ${rows} 行 × ${cols} 列 · 0 个占位 · 联合图要求 ${canvasRatio} · 每格首帧要求 ${ratio}`)).toBeInTheDocument();
  });
  it("12 个分镜分成 9+3 时分别展示两个实际模板，不把总数标为 3×3", async () => {
    vi.spyOn(API, "getGridCapability").mockResolvedValue({
      large_grid_allowed: false, max_cell_count: 9, video_aspect_ratio: "9:16",
      layouts: [
        { scene_count: 9, grid_size: "grid_9", rows: 3, cols: 3, cell_count: 9, placeholder_count: 0, grid_aspect_ratio: "9:16" },
        { scene_count: 3, grid_size: "grid_3", rows: 2, cols: 2, cell_count: 4, placeholder_count: 1, grid_aspect_ratio: "9:16" },
      ],
    });
    renderView(makeSegments(12));
    expect(await screen.findByText("组 #1 · 12 个分镜 · 生成方案：2 张联合图")).toBeInTheDocument();
    expect(screen.getByText(/^第 1 张：9 个分镜 · 3 行 × 3 列/)).toBeInTheDocument();
    expect(screen.getByText(/^第 2 张：3 个分镜 · 2 行 × 2 列/)).toBeInTheDocument();
    expect(screen.getByText(/^首帧已就绪 0\/12.*生成方案 2 张联合图/)).toBeInTheDocument();
  });
  it("能力请求失败时说明尚无布局，不猜测行列和张数", async () => {
    vi.spyOn(API, "getGridCapability").mockRejectedValue(new Error("boom"));
    renderView(makeSegments(12));
    expect(await screen.findByText(/布局能力尚未加载/)).toBeInTheDocument();
    expect(screen.queryByText(/3×3|3 行 × 3 列/)).not.toBeInTheDocument();
    expect(screen.getByText(/^首帧已就绪 0\/12.*生成方案 — 张联合图/)).toBeInTheDocument();
  });

  it("项目视频比例改变后重新读取布局，不沿用竖屏模板", async () => {
    const project = { title: "Demo", content_mode: "narration" as const, style: "", episodes: [], characters: {}, aspect_ratio: "9:16" };
    useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: project });
    vi.spyOn(API, "getGridCapability")
      .mockResolvedValueOnce({
        large_grid_allowed: true, max_cell_count: 25, video_aspect_ratio: "9:16",
        layouts: [{ scene_count: 12, grid_size: "grid_12", rows: 3, cols: 4, cell_count: 12, placeholder_count: 0, grid_aspect_ratio: "3:4" }],
      })
      .mockResolvedValueOnce({
        large_grid_allowed: true, max_cell_count: 25, video_aspect_ratio: "16:9",
        layouts: [{ scene_count: 12, grid_size: "grid_12", rows: 4, cols: 3, cell_count: 12, placeholder_count: 0, grid_aspect_ratio: "4:3" }],
      });
    renderView(makeSegments(12));
    expect(await screen.findByText(/3 行 × 4 列.*首帧要求 9:16/)).toBeInTheDocument();
    useProjectsStore.setState({ currentProjectData: { ...project, aspect_ratio: "16:9" } });
    expect(await screen.findByText(/4 行 × 3 列.*首帧要求 16:9/)).toBeInTheDocument();
    expect(screen.queryByText(/首帧要求 9:16/)).not.toBeInTheDocument();
  });
});

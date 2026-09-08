import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { groupBySegmentBreak, planGridChunks, matchGridsForGroup } from "@/utils/grid-layout";
import { GridPreviewPanel } from "@/components/canvas/timeline/GridPreviewPanel";
import type { GridCapability, GridGeneration } from "@/types/grid";
import type { NarrationSegment, DramaScene } from "@/types";

type Segment = NarrationSegment | DramaScene;

interface GridPreviewViewProps {
  projectName: string;
  episode: number;
  scriptFile?: string;
  segments: Segment[];
  contentMode: "narration" | "drama";
  onGenerateGrid?: (
    episode: number,
    scriptFile: string,
    sceneIds?: string[],
  ) => Promise<void> | void;
}

function getSegmentId(seg: Segment, mode: "narration" | "drama"): string {
  return mode === "narration"
    ? (seg as NarrationSegment).segment_id
    : (seg as DramaScene).scene_id;
}

export function GridPreviewView({
  projectName,
  episode,
  scriptFile,
  segments,
  contentMode,
  onGenerateGrid,
}: GridPreviewViewProps) {
  const { t } = useTranslation("dashboard");
  const gridsRevision = useAppStore((s) => s.gridsRevision);
  const [gridResult, setGridResult] = useState<{ project: string; grids: GridGeneration[] } | null>(null);
  const grids = useMemo(() => gridResult?.project === projectName ? gridResult.grids : [], [gridResult, projectName]);
  const [refreshKey, setRefreshKey] = useState(0);
  const [generatingGroups, setGeneratingGroups] = useState<Set<string>>(new Set());
  const capabilityKey = useProjectsStore((state) => {
    const project = state.currentProjectName === projectName ? state.currentProjectData : null;
    return JSON.stringify([
      project?.aspect_ratio, project?.image_backend, project?.default_image_backend,
      project?.image_provider_t2i, project?.image_provider_i2i, project?.model_settings,
    ]);
  });
  const [capabilityResult, setCapabilityResult] = useState<{ project: string; key: string; value: GridCapability } | null>(null);
  const capability = capabilityResult?.project === projectName && capabilityResult.key === capabilityKey
    ? capabilityResult.value : null;

  const groups = useMemo(() => groupBySegmentBreak(segments), [segments]);

  useEffect(() => {
    if (!projectName) return;
    const controller = new AbortController();
    API.getGridCapability(projectName, { signal: controller.signal })
      .then((cap) => {
        if (controller.signal.aborted) return;
        setCapabilityResult({ project: projectName, key: capabilityKey, value: cap });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [projectName, capabilityKey, gridsRevision]);

  const refreshGrids = useCallback(() => {
    setRefreshKey((v) => v + 1);
  }, []);

  useEffect(() => {
    if (!projectName) return;
    const controller = new AbortController();
    API.listGrids(projectName, { signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setGridResult({ project: projectName, grids: data });
      })
      .catch(() => {});
    return () => controller.abort();
  }, [projectName, refreshKey, gridsRevision]);

  const getGridIdsForGroup = useCallback(
    (groupSegs: Segment[]): string[] =>
      matchGridsForGroup(
        grids,
        groupSegs.map((s) => getSegmentId(s, contentMode)),
        episode,
      ).map((g) => g.id),
    [grids, episode, contentMode],
  );

  const handleGenerateGroup = useCallback(
    // group key 用 sceneIds 排序后 join，分组重排时 spinner 不会挂错卡片
    async (groupKey: string, group: Segment[]) => {
      if (!onGenerateGrid || !scriptFile) return;
      const sceneIds = group.map((s) => getSegmentId(s, contentMode));
      setGeneratingGroups((prev) => new Set(prev).add(groupKey));
      try {
        await onGenerateGrid(episode, scriptFile, sceneIds);
      } finally {
        setGeneratingGroups((prev) => {
          const next = new Set(prev);
          next.delete(groupKey);
          return next;
        });
        refreshGrids();
      }
    },
    [onGenerateGrid, scriptFile, contentMode, episode, refreshGrids],
  );

  const stats = useMemo(() => {
    const plans = groups.map((group) => planGridChunks(group.length, capability));
    const batches = plans.every((plan) => plan !== null)
      ? plans.reduce((sum, plan) => sum + plan.length, 0)
      : "—";
    const cells = segments.length;
    const readyScenes = new Set<string>();
    const generatedGrids = new Set<string>();
    const currentGrids = new Set<string>();
    for (const group of groups) {
      const sceneIds = group.map((s) => getSegmentId(s, contentMode));
      const groupGrids = matchGridsForGroup(grids, sceneIds, episode);
      for (const g of groupGrids) {
        currentGrids.add(g.id);
        if (g.status === "completed" && g.readiness?.image_exists) generatedGrids.add(g.id);
        for (const id of g.readiness?.ready_scene_ids ?? []) {
          if (sceneIds.includes(id)) readyScenes.add(id);
        }
      }
    }
    return { batches, cells, ready: readyScenes.size, generated: generatedGrids.size, total: currentGrids.size };
  }, [groups, segments, grids, episode, contentMode, capability]);

  if (segments.length === 0) {
    return (
      <div
        className="flex h-full items-center justify-center text-sm"
        style={{ color: "var(--color-text-4)" }}
      >
        {t("grid_preview_empty_episode")}
      </div>
    );
  }

  const canGenerate = Boolean(onGenerateGrid && scriptFile);

  return (
    <div className="h-full overflow-y-auto px-5 py-4">
      <div
        className="mb-4 flex flex-wrap items-center gap-2 rounded-md border px-3.5 py-2.5"
        style={{
          borderColor: "var(--color-hairline-soft)",
          background: "oklch(0.18 0.010 265 / 0.5)",
        }}
      >
        <span
          className="num text-[11.5px] tabular-nums"
          style={{ color: "var(--color-text-3)", fontFamily: "var(--font-mono)" }}
        >
          {t("grid_preview_summary", stats)}
        </span>
      </div>

      <div className="flex flex-col gap-3">
        {groups.map((group, idx) => {
          const chunks = planGridChunks(group.length, capability);
          const ids = getGridIdsForGroup(group);
          const groupKey = group
            .map((s) => getSegmentId(s, contentMode))
            .sort()
            .join(",");
          const generating = generatingGroups.has(groupKey);
          return (
            <div
              key={groupKey || idx}
              className="overflow-hidden rounded-md border"
              style={{
                borderColor: "var(--color-hairline-soft)",
                background: "oklch(0.20 0.011 265 / 0.35)",
              }}
            >
              <div
                className="flex items-center gap-2 px-4 py-2"
                style={{ borderBottom: "1px solid var(--color-hairline-soft)" }}
              >
                <span
                  className="num text-[11px] font-semibold uppercase tracking-wider"
                  style={{
                    color: "var(--color-text-3)",
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.6px",
                  }}
                >
                  {t("grid_preview_batch_card_title", {
                    index: idx + 1,
                    cellCount: group.length,
                    batches: chunks?.length ?? "—",
                  })}
                </span>
                <span className="flex-1" />
                {canGenerate && (
                  <button
                    type="button"
                    onClick={() => void handleGenerateGroup(groupKey, group)}
                    disabled={generating}
                    className="sv-navbtn inline-flex items-center gap-1.5"
                  >
                    {generating ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Sparkles className="h-3 w-3" />
                    )}
                    <span>
                      {generating
                        ? t("submitting")
                        : ids.length > 0
                          ? t("grid_regenerate_group_btn")
                          : t("grid_preview_batch_generate")}
                    </span>
                  </button>
                )}
              </div>
              <div className="space-y-1 px-4 py-2 text-xs" style={{ color: "var(--color-text-3)" }}>
                <p>{ids.length > 0 ? t("grid_regeneration_plan") : t("grid_generation_plan")}</p>
                {chunks ? chunks.map((chunk, chunkIndex) => (
                  <p key={chunkIndex}>
                    {t("grid_preview_chunk_plan", {
                      index: chunkIndex + 1,
                      scenes: chunk.scene_count,
                      rows: chunk.rows,
                      cols: chunk.cols,
                      placeholders: chunk.placeholder_count,
                      canvasRatio: chunk.grid_aspect_ratio,
                      frameRatio: capability?.video_aspect_ratio,
                    })}
                  </p>
                )) : <p>{t("grid_preview_layout_unavailable")}</p>}
              </div>
              <GridPreviewPanel
                projectName={projectName}
                gridIds={ids}
                layouts={capability?.layouts}
                videoAspectRatio={capability?.video_aspect_ratio}
                onRegenerated={refreshGrids}
                refreshKey={refreshKey}
                defaultExpanded
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

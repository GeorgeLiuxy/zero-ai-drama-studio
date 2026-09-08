import { useState, useEffect, useRef } from "react";
import {
  ChevronRight,
  RefreshCw,
  Loader2,
  Grid2x2,
  Film,
  AlertCircle,
  CheckCircle2,
  Clock,
  Scissors,
  Upload,
  User,
  Search,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { enqueueGridRegenerate } from "@/actions/generation";
import { errMsg } from "@/utils/async";
import { useProjectsStore } from "@/stores/projects-store";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { GridSplitReview } from "@/components/canvas/timeline/GridSplitReview";
import type { GridGeneration, GridLayoutPlan, GridSplitPreview, ReferenceImage } from "@/types/grid";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GridPreviewPanelProps {
  projectName: string;
  gridIds: string[];
  layouts?: GridLayoutPlan[];
  videoAspectRatio?: string;
  /** Called after a regeneration is submitted (for parent to refresh grids list). */
  onRegenerated?: () => void;
  /** Changed when grids list is refreshed, triggers re-fetch of panel data. */
  refreshKey?: number;
  /** Render in expanded state on first mount. Used by the dedicated grid preview tab. */
  defaultExpanded?: boolean;
}

function canvasRatio(grid: GridGeneration): string {
  const [width, height] = (grid.video_aspect_ratio ?? "").split(":").map(Number);
  const x = width * grid.cols;
  const y = height * grid.rows;
  if (![x, y].every((value) => Number.isSafeInteger(value) && value > 0)) return "—";
  let divisor = x;
  let remainder = y;
  while (remainder !== 0) [divisor, remainder] = [remainder, divisor % remainder];
  return `${x / divisor}:${y / divisor}`;
}

// ---------------------------------------------------------------------------
// StatusBadge
// ---------------------------------------------------------------------------

type GridStatus = GridGeneration["status"];

function StatusBadge({ status, t }: { status: GridStatus; t: (key: string) => string }) {
  const STATUS_KEY: Record<GridStatus, string> = {
    pending: "grid_status_pending",
    generating: "grid_status_generating",
    completed: "grid_status_completed",
    failed: "grid_status_failed",
  };

  const configs: Record<
    GridStatus,
    { icon: React.ReactNode; cls: string }
  > = {
    pending: {
      icon: <Clock className="h-3 w-3" />,
      cls: "bg-gray-800/80 text-gray-400 border-gray-700/50",
    },
    generating: {
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
      cls: "bg-blue-950/60 text-blue-300 border-blue-700/40",
    },
    completed: {
      icon: <CheckCircle2 className="h-3 w-3" />,
      cls: "bg-emerald-950/60 text-emerald-400 border-emerald-700/40",
    },
    failed: {
      icon: <AlertCircle className="h-3 w-3" />,
      cls: "bg-red-950/60 text-red-400 border-red-700/40",
    },
  };

  const { icon, cls } = configs[status];
  const label = t(STATUS_KEY[status]);

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium tracking-wide ${cls}`}
    >
      {icon}
      {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ReferenceImageStrip
// ---------------------------------------------------------------------------

function ReferenceImageStrip({
  references,
  projectName,
  refreshKey,
}: {
  references: ReferenceImage[];
  projectName: string;
  refreshKey: number;
}) {
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  return (
    <div className="flex gap-2.5 overflow-x-auto pb-1 scrollbar-thin">
      {references.map((ref, idx) => {
        const isChar = ref.ref_type === "character";
        const cacheBust = fingerprints[ref.path] ?? refreshKey;
        return (
          <motion.div
            key={ref.path}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: idx * 0.05, duration: 0.2 }}
            className="group flex w-14 shrink-0 flex-col items-center gap-1"
          >
            <div
              className={`w-full overflow-hidden rounded border bg-gray-900/50 transition-all duration-200 ${
                isChar
                  ? "border-amber-800/30 group-hover:border-amber-500/50"
                  : "border-sky-800/30 group-hover:border-sky-500/50"
              }`}
            >
              <img
                src={API.getFileUrl(projectName, ref.path, cacheBust)}
                alt={ref.name}
                className="block aspect-square w-full object-cover transition-transform duration-200 group-hover:scale-105"
              />
            </div>
            <div className="flex max-w-full items-center gap-0.5">
              {isChar ? (
                <User className="h-2 w-2 shrink-0 text-amber-500/50" />
              ) : (
                <Search className="h-2 w-2 shrink-0 text-sky-500/50" />
              )}
              <span
                className={`truncate text-[8px] leading-tight ${
                  isChar ? "text-amber-400/50" : "text-sky-400/50"
                }`}
                title={ref.name}
              >
                {ref.name}
              </span>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// GridPreviewPanel
// ---------------------------------------------------------------------------

export function GridPreviewPanel({
  projectName,
  gridIds,
  layouts,
  videoAspectRatio,
  onRegenerated,
  refreshKey = 0,
  defaultExpanded = false,
}: GridPreviewPanelProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [grid, setGrid] = useState<GridGeneration | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [splitting, setSplitting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [splitReview, setSplitReview] = useState<{
    project: string;
    gridId: string;
    revision: number;
    fingerprint: number | null;
    preview: GridSplitPreview;
  } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewRequest = useRef<AbortController | null>(null);
  const { t } = useTranslation("dashboard");

  const hasGrids = gridIds.length > 0;
  const multipleGrids = gridIds.length > 1;
  const safeIdx = Math.min(selectedIdx, Math.max(0, gridIds.length - 1));
  const selectedGridId = gridIds[safeIdx] ?? null;

  // 全局资源变更和父级主动刷新均重拉详情，正式首帧状态由后端重新检查。
  const gridsRevision = useAppStore((s) => s.gridsRevision);

  // safeIdx already clamps selectedIdx to valid range; no effect needed

  // Fetch grid data when expanded and selectedGridId is available
  useEffect(() => {
    if (!expanded || !selectedGridId) return;

    let cancelled = false;
    // Clear stale data and show spinner when switching batches
    if (!grid || grid.id !== selectedGridId) {
      // 切换批次时清空旧数据并展示加载状态，再触发异步 fetch。清空同时会卸载下方的
      // 版本时光机——它按 resourceId 拉版本列表且无取消，若改成加载期间留着旧数据渲染，
      // 上一张在途的版本列表会落进新宫格的面板，选中即按新宫格 ID + 旧版本号发起还原。
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLoading(true);
      setGrid(null);
    }
    setError(null);
    setPreviewing(false);
    setSplitReview(null);

    API.getGrid(projectName, selectedGridId)
      .then((data) => {
        if (!cancelled) {
          setGrid(data);
          setSplitReview(null);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setError(errMsg(err, t("grid_load_failed")));
          setLoading(false);
        }
      });

    return () => {
      cancelled = true;
      previewRequest.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- grid 仅用于切换批次判断；t 稳定
  }, [expanded, selectedGridId, projectName, gridsRevision, refreshKey]);

  // 占用判定接入 live tasks store：与同页兄弟控件同源，
  // 不再依赖本地 grid.status 快照（刷新才更新，提交后到下次 fetch 之间会误判为空闲）。
  const activeGridIds = useActiveResourceIds("grid", projectName);
  const isInProgress = selectedGridId != null && activeGridIds.has(selectedGridId);
  // 面板动作（重生成/切分/上传/版本恢复）互斥：都写同一份联合图或分镜格，
  // 任一在途时兄弟控件同步禁用。
  const actionBusy = regenerating || splitting || previewing || uploading || restoring || isInProgress;

  // 提交时刻的占用新鲜读：渲染快照（isInProgress）之外复核 tasks store 最新状态
  const freshBusy = () =>
    selectedGridId != null && isResourceBusy("grid", projectName, selectedGridId);

  const handleSplit = () => {
    if (!grid || !selectedGridId || !currentReview?.valid || actionBusy) return;
    if (freshBusy()) {
      useAppStore.getState().pushToast(t("grid_regenerate_busy"), "error");
      return;
    }
    setSplitting(true);
    API.splitGrid(projectName, selectedGridId, currentReview.review_token)
      .then((res) => {
        setSplitReview(null);
        useProjectsStore.getState().updateAssetFingerprints(res.asset_fingerprints);
        useAppStore.getState().invalidateGrids();
        useAppStore
          .getState()
          .pushToast(t("grid_split_success", { count: res.updated_scene_ids.length }), "success");
        if (res.missing_scene_ids.length > 0) {
          useAppStore
            .getState()
            .pushToast(t("grid_split_missing_skipped", { ids: res.missing_scene_ids.join(", ") }), "error");
        }
      })
      .catch((err: unknown) => {
        setSplitReview(null);
        // 瞬态失败走 toast，不用 setError 毁掉整个面板视图
        useAppStore.getState().pushToast(t("grid_split_failed", { message: errMsg(err) }), "error");
      })
      .finally(() => setSplitting(false));
  };

  const handleUploadFile = (file: File) => {
    if (!selectedGridId || actionBusy) return;
    if (freshBusy()) {
      useAppStore.getState().pushToast(t("grid_regenerate_busy"), "error");
      return;
    }
    setUploading(true);
    setSplitReview(null);
    API.uploadGridImage(projectName, selectedGridId, file)
      .then((res) => {
        useProjectsStore.getState().updateAssetFingerprints(res.asset_fingerprints);
        // 联合图内容与宫格记录（status / split_at）已变，走全局失效信号重拉详情
        useAppStore.getState().invalidateGrids();
        useAppStore.getState().pushToast(t("grid_upload_success"), "success");
      })
      .catch((err: unknown) => {
        useAppStore.getState().pushToast(t("grid_upload_failed", { message: errMsg(err) }), "error");
      })
      .finally(() => setUploading(false));
  };

  // 优先使用持久化的 mtime 指纹做 cache-bust，跨页面刷新仍然有效；
  // 回退到 refreshKey 仅用于指纹尚未送达前的当次会话。
  const gridFp = useProjectsStore((s) =>
    grid?.grid_image_path ? (s.assetFingerprints[grid.grid_image_path] ?? null) : null,
  );
  const currentReview = splitReview?.project === projectName
    && splitReview.gridId === selectedGridId
    && splitReview.revision === gridsRevision
    && splitReview.fingerprint === gridFp
    ? splitReview.preview : null;

  const handlePreviewSplit = () => {
    if (!selectedGridId || actionBusy) return;
    if (freshBusy()) {
      useAppStore.getState().pushToast(t("grid_regenerate_busy"), "error");
      return;
    }
    setPreviewing(true);
    setSplitReview(null);
    const controller = new AbortController();
    previewRequest.current?.abort();
    previewRequest.current = controller;
    API.getGridSplitPreview(projectName, selectedGridId)
      .then((preview) => {
        if (controller.signal.aborted) return;
        setSplitReview({ project: projectName, gridId: selectedGridId, revision: gridsRevision, fingerprint: gridFp, preview });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        useAppStore.getState().pushToast(t("grid_split_failed", { message: errMsg(err) }), "error");
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewing(false);
      });
  };
  const imageUrl =
    grid?.grid_image_path
      ? API.getFileUrl(projectName, grid.grid_image_path, gridFp ?? refreshKey)
      : null;

  const refs = grid?.reference_images ?? [];
  const plannedLayout = layouts?.find((layout) => layout.scene_count === grid?.scene_ids.length);
  const layoutChanged = grid && plannedLayout && (
    grid.rows !== plannedLayout.rows || grid.cols !== plannedLayout.cols
    || grid.video_aspect_ratio !== videoAspectRatio
  );
  const readyCount = grid ? new Set((grid.readiness?.ready_scene_ids ?? []).filter((id) => grid.scene_ids.includes(id))).size : 0;

  return (
    <div>
      {/* Toggle header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 border-t border-amber-800/20 px-4 py-1.5 text-left transition-colors hover:bg-amber-900/10 focus-ring"
      >
        <motion.span
          animate={{ rotate: expanded ? 90 : 0 }}
          transition={{ duration: 0.18, ease: "easeInOut" }}
          className="text-amber-600/70"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </motion.span>

        <Film className="h-3.5 w-3.5 text-amber-500/60" />

        <span className="text-xs font-medium text-amber-400/70">{t("grid_preview_title")}</span>

        {!hasGrids && (
          <span className="ml-1 text-[10px] text-gray-600">{t("grid_not_generated")}</span>
        )}

        {multipleGrids && !expanded && (
          <span className="ml-1 text-[10px] text-gray-600">
            {t("grid_batch_unit", { count: gridIds.length })}
          </span>
        )}
      </button>

      {/* Collapsible content */}
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            key="panel-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-3 pt-2">
              {/* No grid yet */}
              {!hasGrids ? (
                <div className="flex flex-col items-center gap-2 py-6 text-center">
                  <Grid2x2 className="h-8 w-8 text-gray-700" />
                  <p className="text-xs text-gray-600">{t("grid_no_grids_yet")}</p>
                  <p className="text-[10px] text-gray-700">
                    {t("grid_generate_instruction")}
                  </p>
                </div>
              ) : loading && !grid ? (
                /* Loading state (initial) */
                <div className="flex items-center justify-center gap-2 py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-amber-500/50" />
                  <span className="text-xs text-gray-600">{t("grid_loading_data")}</span>
                </div>
              ) : error ? (
                /* Error state */
                <div className="flex items-center gap-2 rounded-md border border-red-900/30 bg-red-950/20 px-3 py-2.5">
                  <AlertCircle className="h-4 w-4 shrink-0 text-red-500/70" />
                  <span className="text-xs text-red-400/80">{error}</span>
                </div>
              ) : grid ? (
                /* Grid loaded */
                <div className="flex flex-col gap-3">
                  {/* Top bar: batch pills + status + regen */}
                  <div className="flex flex-wrap items-center gap-2">
                    {multipleGrids && (
                      <div className="flex items-center gap-0.5 rounded-md bg-gray-900/50 p-0.5">
                        {gridIds.map((_, idx) => (
                          <button
                            key={idx}
                            type="button"
                            onClick={() => setSelectedIdx(idx)}
                            aria-pressed={idx === safeIdx}
                            className={`inline-flex h-5 min-w-[1.375rem] items-center justify-center rounded px-1 text-[10px] font-medium tabular-nums transition-all duration-150 ${
                              idx === safeIdx
                                ? "bg-amber-700/50 text-amber-200 shadow-sm"
                                : "text-gray-500 hover:text-gray-300 hover:bg-gray-800/60"
                            }`}
                          >
                            {t("grid_image_page", { index: idx + 1 })}
                          </button>
                        ))}
                      </div>
                    )}

                    <StatusBadge status={grid.status} t={t} />

                    {grid.status === "completed" && grid.grid_image_path && grid.readiness?.image_exists !== false && readyCount < grid.scene_ids.length && grid.readiness?.geometry_valid !== false && (
                      <span className="inline-flex items-center gap-1 rounded border border-violet-700/40 bg-violet-950/60 px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-violet-300">
                        <Scissors className="h-3 w-3" />
                        {t("grid_frames_pending")}
                      </span>
                    )}

                    <span className="text-[10px] text-gray-600">
                      {grid.model}
                    </span>

                    {grid.error_message && (
                      <span
                        className="truncate text-[10px] text-red-400/70"
                        title={grid.error_message}
                      >
                        {grid.error_message}
                      </span>
                    )}

                    <div className="ml-auto flex shrink-0 items-center gap-1">
                      {selectedGridId && (
                        <VersionTimeMachine
                          projectName={projectName}
                          resourceType="grids"
                          resourceId={selectedGridId}
                          iconOnly
                          busy={actionBusy}
                          checkBusy={freshBusy}
                          onRestoringChange={(value) => {
                            setRestoring(value);
                            if (value) setSplitReview(null);
                          }}
                          onRestore={() => {
                            // 还原换回历史联合图并复位切分态，重拉记录同步 split_at
                            useAppStore.getState().invalidateGrids();
                          }}
                        />
                      )}

                      <input
                        ref={fileInputRef}
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        className="hidden"
                        onChange={(e) => {
                          const file = e.target.files?.[0];
                          // 复位 value：同一文件二次选择也要触发 change
                          e.target.value = "";
                          if (file) handleUploadFile(file);
                        }}
                      />
                      <button
                        type="button"
                        disabled={actionBusy}
                        onClick={() => fileInputRef.current?.click()}
                        className={`shrink-0 whitespace-nowrap inline-flex items-center gap-1 rounded border border-gray-700/50 bg-gray-900/40 px-2 py-1 text-[10px] font-medium text-gray-400 transition-colors ${
                          actionBusy ? "opacity-50 cursor-not-allowed" : "hover:bg-gray-800/60 hover:text-gray-200"
                        }`}
                      >
                        {uploading ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Upload className="h-3 w-3" />
                        )}
                        {uploading ? t("grid_uploading") : t("grid_upload_btn")}
                      </button>

                      <motion.button
                        type="button"
                        disabled={actionBusy || !grid.grid_image_path}
                        onClick={handlePreviewSplit}
                        className={`shrink-0 whitespace-nowrap inline-flex items-center gap-1 rounded border border-amber-800/30 bg-amber-950/30 px-2 py-1 text-[10px] font-medium text-amber-400/80 transition-colors ${
                          actionBusy || !grid.grid_image_path
                            ? "opacity-50 cursor-not-allowed"
                            : "hover:bg-amber-900/40 hover:text-amber-300"
                        }`}
                        whileTap={actionBusy || !grid.grid_image_path ? {} : { scale: 0.95 }}
                      >
                        {splitting || previewing ? (
                          <Loader2 className="h-3 w-3 animate-spin" />
                        ) : (
                          <Scissors className="h-3 w-3" />
                        )}
                        {splitting ? t("grid_splitting") : previewing ? t("grid_review_loading") : t("grid_split_current_btn")}
                      </motion.button>

                      <motion.button
                        type="button"
                        disabled={actionBusy}
                        onClick={() => {
                          if (!selectedGridId || actionBusy) return;
                          // 提交前用 getState() 新鲜读复核：面板停留期间占用状态可能
                          // 已变化，渲染期捕获的 isInProgress 未必反映最新状态。
                          if (freshBusy()) {
                            // 用 toast 而非 setError：error 是面板的整体错误态，会把宫格图、
                            // 批次切换与本按钮一并替换掉，直到下次 refetch 才恢复；占用拒绝是
                            // 瞬态提示，不该毁掉当前视图。
                            useAppStore.getState().pushToast(t("grid_regenerate_busy"), "error");
                            return;
                          }
                          setRegenerating(true);
                          setSplitReview(null);
                          enqueueGridRegenerate(projectName, selectedGridId, grid?.script_file ?? null)
                            .then(() => {
                              setGrid((prev) => prev?.id === selectedGridId ? { ...prev, status: "pending" } : prev);
                              onRegenerated?.();
                            })
                            .catch((err: unknown) => {
                              setError(errMsg(err, t("grid_regenerate_failed")));
                            })
                            .finally(() => setRegenerating(false));
                        }}
                        className={`shrink-0 whitespace-nowrap inline-flex items-center gap-1 rounded border border-amber-800/30 bg-amber-950/30 px-2 py-1 text-[10px] font-medium text-amber-400/80 transition-colors ${
                          actionBusy ? "opacity-50 cursor-not-allowed" : "hover:bg-amber-900/40 hover:text-amber-300"
                        }`}
                        whileTap={actionBusy ? {} : { scale: 0.95 }}
                      >
                        <RefreshCw className={`h-3 w-3 ${regenerating || isInProgress ? "animate-spin" : ""}`} />
                        {regenerating ? t("grid_regenerating") : isInProgress ? t("generating_grid") : t("grid_regenerate_current_btn")}
                      </motion.button>
                    </div>
                  </div>

                  <p className="text-xs text-gray-400">
                    {t("grid_current_scope", { index: safeIdx + 1, total: gridIds.length, scenes: grid.scene_ids.length, ready: readyCount })}
                  </p>
                  {grid.grid_image_path && grid.readiness?.image_exists === false && (
                    <p className="text-xs text-red-300">{t("grid_image_missing")}</p>
                  )}
                  {grid.readiness?.geometry_valid === false && (
                    <p className="text-xs text-red-300">{t("grid_geometry_invalid")}</p>
                  )}
                  {layoutChanged && (
                    <p className="text-xs text-amber-300">
                      {t("grid_layout_changed")}
                    </p>
                  )}

                  {/* Composite image + metadata */}
                  {imageUrl ? (
                    <div className="overflow-hidden rounded-md border border-gray-800/50 bg-gray-900/40">
                      <img
                        src={imageUrl}
                        alt={t("grid_composite_image_alt")}
                        className="block max-h-64 w-full object-contain bg-black/20"
                      />
                      <div className="flex flex-wrap items-center gap-2 border-t border-gray-800/50 px-2.5 py-1.5">
                        <span className="font-mono text-[10px] text-gray-500">
                          {t("grid_frozen_layout", { scenes: grid.scene_ids.length, rows: grid.rows, cols: grid.cols, placeholders: grid.cell_count - grid.scene_ids.length, canvasRatio: canvasRatio(grid) })}
                        </span>
                        <span className="text-[10px] text-gray-700">
                          {grid.video_aspect_ratio && t("grid_first_frame_ratio", { ratio: grid.video_aspect_ratio })}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex h-24 items-center justify-center rounded-md border border-gray-800/40 bg-gray-900/30">
                      <span className="text-[10px] text-gray-700">
                        {grid.status === "generating" || grid.status === "pending"
                          ? t("generating_grid")
                          : t("grid_no_image")}
                      </span>
                    </div>
                  )}

                  {currentReview && <GridSplitReview preview={currentReview} busy={actionBusy} onConfirm={handleSplit} onCancel={() => setSplitReview(null)} />}

                  {/* Reference images strip */}
                  {refs.length > 0 && (
                    <div className="flex flex-col gap-1.5">
                      <span className="text-[9px] font-medium uppercase tracking-widest text-gray-600">
                        {t("grid_reference_images")}
                      </span>
                      <ReferenceImageStrip
                        references={refs}
                        projectName={projectName}
                        refreshKey={refreshKey}
                      />
                    </div>
                  )}
                </div>
              ) : null}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

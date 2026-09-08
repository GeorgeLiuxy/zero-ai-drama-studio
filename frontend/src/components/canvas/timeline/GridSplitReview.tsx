import { useTranslation } from "react-i18next";
import type { GridSplitPreview } from "@/types/grid";

const ISSUE_KEYS: Record<string, string> = {
  project_aspect_changed: "grid_review_issue_project_aspect_changed",
  scene_mapping_mismatch: "grid_review_issue_scene_mapping_mismatch",
  grid_aspect_ratio_mismatch: "grid_review_issue_grid_aspect_ratio_mismatch",
  invalid_grid_dimensions: "grid_review_issue_invalid_grid_dimensions",
  invalid_cell_dimensions: "grid_review_issue_invalid_cell_dimensions",
  invalid_aspect_ratio: "grid_review_issue_invalid_aspect_ratio",
  unexpected_horizontal_separator: "grid_review_issue_unexpected_horizontal_separator",
  unexpected_vertical_separator: "grid_review_issue_unexpected_vertical_separator",
  visible_horizontal_separator: "grid_review_issue_visible_horizontal_separator",
  visible_vertical_separator: "grid_review_issue_visible_vertical_separator",
};

export function GridSplitReview({
  preview,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: GridSplitPreview;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const upscaled = preview.cell_width < preview.output_width || preview.cell_height < preview.output_height;
  return (
    <section aria-label={t("grid_review_title")} className="space-y-3 rounded-md border border-amber-700/40 bg-gray-950/50 p-3">
      <h3 className="text-sm font-semibold text-amber-200">{t("grid_review_title")}</h3>
      <p className="text-xs text-gray-300">{t("grid_review_instruction")}</p>
      <p className="text-xs text-gray-400">
        {t("grid_review_dimensions", {
          width: preview.width,
          height: preview.height,
          cellWidth: preview.cell_width,
          cellHeight: preview.cell_height,
          outputWidth: preview.output_width,
          outputHeight: preview.output_height,
          ratio: preview.video_aspect_ratio,
        })}
      </p>
      {upscaled && <p className="text-xs text-amber-300">{t("grid_review_upscale_warning")}</p>}
      {!preview.valid && <p className="text-xs text-red-300">{t("grid_review_invalid")}</p>}
      {preview.issues.length > 0 && (
        <ul className="list-inside list-disc text-xs text-red-300">
          {preview.issues.map((issue) => (
            <li key={issue}>{ISSUE_KEYS[issue] ? t(ISSUE_KEYS[issue]) : t("grid_review_issue_unknown", { issue })}</li>
          ))}
        </ul>
      )}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-3">
        {preview.cells.map((cell) => (
          <figure key={cell.index} className="overflow-hidden rounded border border-gray-700 bg-black/30">
            <img src={cell.image} alt={t("grid_review_cell_alt", { index: cell.index + 1, scene: cell.scene_id ?? t("grid_review_placeholder") })} className="mx-auto block max-h-96 w-full object-contain" />
            <figcaption className="px-2 py-1 text-xs text-gray-300">
              {t("grid_review_cell_label", { row: cell.row + 1, col: cell.col + 1, scene: cell.placeholder ? t("grid_review_placeholder") : cell.scene_id })}
            </figcaption>
          </figure>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onConfirm} disabled={busy || !preview.valid} className="rounded border border-amber-700 px-3 py-2 text-xs text-amber-200 disabled:cursor-not-allowed disabled:opacity-40">
          {t("grid_review_confirm")}
        </button>
        <button type="button" onClick={onCancel} disabled={busy} className="rounded border border-gray-700 px-3 py-2 text-xs text-gray-300 disabled:opacity-40">
          {t("grid_review_cancel")}
        </button>
      </div>
    </section>
  );
}

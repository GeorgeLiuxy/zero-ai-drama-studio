/**
 * Grid image-to-video type definitions.
 *
 * Maps to backend models in lib/grid_manager.py and server/routers/grids.py.
 */

export interface ReferenceImage {
  path: string;
  name: string;
  ref_type: "character" | "scene" | "prop";
}

export interface FrameCell {
  index: number;
  row: number;
  col: number;
  frame_type: "first" | "transition" | "placeholder";
  prev_scene_id: string | null;
  next_scene_id: string | null;
  image_path: string | null;
}

export interface GridLayoutPlan {
  scene_count: number;
  grid_size: string;
  rows: number;
  cols: number;
  cell_count: number;
  placeholder_count: number;
  grid_aspect_ratio: string;
}

/** 布局表由后端按当前视频比例与生成能力计算，前端不自行选择模板。 */
export interface GridCapability {
  large_grid_allowed: boolean;
  max_cell_count: number;
  video_aspect_ratio: string;
  layouts: GridLayoutPlan[];
}

export interface GridSplitPreview {
  review_token: string;
  valid: boolean;
  issues: string[];
  width: number;
  height: number;
  cell_width: number;
  cell_height: number;
  output_width: number;
  output_height: number;
  video_aspect_ratio: string;
  cells: {
    index: number;
    row: number;
    col: number;
    scene_id: string | null;
    placeholder: boolean;
    image: string;
  }[];
}

export interface GridGeneration {
  id: string;
  episode: number;
  script_file: string;
  scene_ids: string[];
  grid_image_path: string | null;
  rows: number;
  cols: number;
  cell_count: number;
  frame_chain: FrameCell[];
  /** 联合图生命周期：completed 仅表示联合图就绪，是否已落格由 split_at 表达 */
  status: "pending" | "generating" | "completed" | "failed";
  prompt: string | null;
  provider: string;
  model: string;
  grid_size: string;
  video_aspect_ratio?: string | null;
  created_at: string;
  error_message: string | null;
  reference_images?: ReferenceImage[] | null;
  /** 最近一次按当前联合图切分落格的时间；联合图内容变更（重生成/上传/还原）后为 null */
  split_at: string | null;
}

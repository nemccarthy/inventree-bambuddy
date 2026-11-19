export interface Permissions {
  view: boolean;
  link: boolean;
  print: boolean;
  manage: boolean;
}

export interface Settings {
  auto_low_stock: boolean;
  auto_build_demand: boolean;
  include_pending: boolean;
  max_plates: number;
  public_url: string;
  configured: boolean;
  events_enabled: boolean;
  schedule_enabled: boolean;
}

export interface Filament {
  type: string | null;
  color: string | null;
  grams: number | null;
}

export interface PartLabel {
  pk: number;
  name: string;
  ipn: string;
  description: string;
  units: string;
  active: boolean;
  assembly: boolean;
  trackable: boolean;
  minimum_stock: number;
  default_location: number | null;
}

export interface Link {
  file_id: number;
  filename: string | null;
  folder: string | null;
  plate_id: number | null;
  plate_name: string | null;
  parts_per_plate: number;
  printer_id: number | null;
  printer_name: string | null;
  target_model: string | null;
  target_location: string | null;
  auto: boolean;
  restock_to: number | null;
  max_plates: number | null;
  destination: number | null;
  destination_name: string | null;
  print_name: string | null;
  sliced_for_model: string | null;
  print_time_seconds: number | null;
  filament_grams: number | null;
  filaments: Filament[];
  has_thumbnail: boolean;
}

export interface Numbers {
  stock: number;
  incoming: number;
  incoming_runs: number;
  demand: number;
  demand_builds: { pk: number; reference: string; quantity: number }[];
  minimum: number;
}

export interface Decision {
  plates: number;
  quantity: number;
  reason: 'low_stock' | 'build_demand' | null;
  projected: number;
  floor: number;
  target: number;
  shortfall: number;
  capped: boolean;
  enabled: boolean;
}

export interface QueueItem {
  id: number;
  status: string;
  manual_start?: boolean;
  counted?: boolean;
  printer_name?: string | null;
  target_model?: string | null;
  waiting_reason?: string | null;
  filament_short?: boolean;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
}

export type RunState =
  | 'staged'
  | 'queued'
  | 'printing'
  | 'printed'
  | 'short'
  | 'failed'
  | 'done'
  | 'cancelled';

export interface RunSummary {
  state: RunState;
  counts: Record<string, number>;
  plates: number;
  remaining: number;
  awaiting_plates: number;
  awaiting_quantity: number;
  shortfall_plates: number;
  can_release: boolean;
  can_confirm: boolean;
  can_reprint: boolean;
  can_close: boolean;
  can_cancel: boolean;
}

export interface Run {
  build: number;
  reference: string;
  title: string;
  part: number;
  part_name: string;
  part_ipn: string;
  quantity: number;
  completed: number;
  build_status: number;
  created: string | null;
  target_date: string | null;
  issued_by: string | null;
  destination: number | null;
  destination_name: string | null;
  source: 'manual' | 'low_stock' | 'build_demand';
  trigger_build: { pk: number; reference: string } | null;
  file_id: number | null;
  filename: string | null;
  plate_id: number | null;
  parts_per_plate: number;
  printer_name: string | null;
  target_model: string | null;
  target_location: string | null;
  print_time_seconds: number | null;
  batch_id: number | null;
  items: QueueItem[];
  confirmations: { good: number; plates: number; by: string | null; at: string }[];
  checked: string | null;
  error: string | null;
  summary: RunSummary;
}

export interface PartData {
  part: PartLabel;
  link: Link | null;
  numbers: Numbers;
  decision: Decision | null;
  runs: Run[];
  history: Run[];
  queue_error: string | null;
  permissions: Permissions;
  settings: Settings;
}

export interface OverviewPart {
  part: PartLabel;
  link: Link;
  numbers: Numbers;
  decision: Decision;
  open_runs: number;
}

export interface OverviewData {
  parts: OverviewPart[];
  runs: Run[];
  history: Run[];
  queue_error: string | null;
  permissions: Permissions;
  settings: Settings;
}

export interface Printer {
  id: number;
  name: string;
  model: string | null;
  location: string | null;
  active: boolean;
}

export interface ConfigData {
  version: string;
  permissions: Permissions;
  settings: Settings;
  bambuddy: {
    configured: boolean;
    ok: boolean;
    error: string | null;
    version: string | null;
    outdated: boolean;
    printers: Printer[];
  };
}

export interface LibraryFile {
  id: number;
  filename: string;
  folder: string | null;
  print_name: string | null;
  print_time_seconds: number | null;
  filament_grams: number | null;
  sliced_for_model: string | null;
  has_thumbnail: boolean;
  linked_to: { pk: number; name: string; ipn: string; plate_id: number | null }[];
}

export interface FilePlate {
  index: number;
  name: string | null;
  objects: number | null;
  print_time_seconds: number | null;
  filament_grams: number | null;
  filaments: Filament[];
}

export interface FileDetail {
  id: number;
  filename: string;
  sliced: boolean;
  sliced_for_model: string | null;
  print_time_seconds: number | null;
  filament_grams: number | null;
  has_thumbnail: boolean;
  plates: FilePlate[];
}

export interface CheckResult {
  enabled: boolean;
  dry_run: boolean;
  results: {
    part: number;
    name: string;
    ipn: string;
    plates: number;
    quantity: number;
    reason: string | null;
    capped: boolean;
    build?: number;
    reference?: string;
    error?: string;
  }[];
}

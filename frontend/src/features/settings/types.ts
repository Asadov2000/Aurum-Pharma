export type ThemePreference = "system" | "light" | "dark";
export type DensityPreference = "auto" | "compact" | "comfortable" | "touch";
export type ContrastPreference = "standard" | "high";
export type AccentPreference = "teal" | "blue" | "violet" | "green" | "amber" | "rose";

export interface WorkspacePreferences {
  desktop_mode: "auto" | "compact" | "expanded";
  hidden_routes: string[];
  favorite_routes: string[];
  route_order: string[];
  start_route: string;
}

export interface UserPreferences {
  theme: ThemePreference;
  density: DensityPreference;
  contrast: ContrastPreference;
  reduce_motion: boolean;
  accent: AccentPreference;
  workspace: WorkspacePreferences;
  version: number;
  updated_at: string;
}

export interface UserPreferencesUpdate {
  expected_version: number;
  theme?: ThemePreference;
  density?: DensityPreference;
  contrast?: ContrastPreference;
  reduce_motion?: boolean;
  accent?: AccentPreference;
  workspace?: WorkspacePreferences;
}

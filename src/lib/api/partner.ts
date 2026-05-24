import { http } from "./core";

export type MoodState = {
  mood: "calm" | "curious" | "tender" | "reflective" | "energized";
  mood_intensity: number;
  updated_at: string | null;
};

export function getCurrentMood(): Promise<MoodState> {
  return http<MoodState>("/api/partner/mood");
}

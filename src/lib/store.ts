// =============================================================================
// Ranking System Store (localStorage for MVP, Supabase later)
// =============================================================================
// Provides CRUD operations for ranking systems.
// In Milestone 1, everything is stored in localStorage.
// When Supabase is connected, this module will switch to DB operations.
// =============================================================================

import type { RankingSystem } from "@/types";
import { DEFAULT_RANKING_SYSTEM } from "./ranking-engine";

const STORAGE_KEY = "korean-stock-ranker:systems";

/**
 * Get all saved ranking systems.
 */
export function getSavedSystems(): RankingSystem[] {
  if (typeof window === "undefined") return [DEFAULT_RANKING_SYSTEM];

  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // Seed with default system
      saveSystems([DEFAULT_RANKING_SYSTEM]);
      return [DEFAULT_RANKING_SYSTEM];
    }
    return JSON.parse(raw);
  } catch {
    return [DEFAULT_RANKING_SYSTEM];
  }
}

/**
 * Get a single ranking system by ID.
 */
export function getSystemById(id: string): RankingSystem | undefined {
  return getSavedSystems().find(s => s.id === id);
}

/**
 * Save all systems (overwrites).
 */
function saveSystems(systems: RankingSystem[]): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(systems));
}

/**
 * Create or update a ranking system.
 */
export function upsertSystem(system: RankingSystem): void {
  const systems = getSavedSystems();
  const idx = systems.findIndex(s => s.id === system.id);
  if (idx >= 0) {
    systems[idx] = { ...system, updatedAt: new Date().toISOString() };
  } else {
    systems.push({ ...system, createdAt: new Date().toISOString(), updatedAt: new Date().toISOString() });
  }
  saveSystems(systems);
}

/**
 * Delete a ranking system.
 */
export function deleteSystem(id: string): void {
  const systems = getSavedSystems().filter(s => s.id !== id);
  saveSystems(systems);
}

/**
 * Duplicate a ranking system with a new ID and name.
 */
export function duplicateSystem(id: string): RankingSystem | null {
  const original = getSystemById(id);
  if (!original) return null;

  const copy: RankingSystem = {
    ...JSON.parse(JSON.stringify(original)),
    id: crypto.randomUUID(),
    name: `${original.name} (Copy)`,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };

  upsertSystem(copy);
  return copy;
}

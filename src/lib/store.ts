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

// Ranking-system IDs that were used to seed the default system in earlier
// versions of the app, before it was renamed to DEFAULT_RANKING_SYSTEM.id
// ("p123-inspired"). Browsers seeded before that rename still hold a system
// with one of these IDs, and clicking "Run" queries the DB for a snapshot
// under that stale ID — which never exists — producing a 404. We remap them
// on read so existing visitors don't have to clear localStorage manually.
const LEGACY_DEFAULT_IDS = ["default"];

/**
 * Reconcile saved systems with the current default system.
 *
 * - Any system whose ID is a legacy default ID is replaced with the current
 *   DEFAULT_RANKING_SYSTEM (its tree/options are stale anyway).
 * - Duplicates by ID are removed.
 * - Guarantees the current default system is present.
 *
 * Returns the (possibly unchanged) list plus a `changed` flag so the caller
 * can persist only when a migration actually happened.
 */
function migrateSystems(systems: RankingSystem[]): { systems: RankingSystem[]; changed: boolean } {
  let changed = false;

  const remapped = systems.map((s) => {
    if (LEGACY_DEFAULT_IDS.includes(s.id)) {
      changed = true;
      return DEFAULT_RANKING_SYSTEM;
    }
    return s;
  });

  const seen = new Set<string>();
  const deduped = remapped.filter((s) => {
    if (seen.has(s.id)) {
      changed = true;
      return false;
    }
    seen.add(s.id);
    return true;
  });

  if (!seen.has(DEFAULT_RANKING_SYSTEM.id)) {
    deduped.unshift(DEFAULT_RANKING_SYSTEM);
    changed = true;
  }

  return { systems: deduped, changed };
}

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

    const parsed = JSON.parse(raw) as RankingSystem[];
    if (!Array.isArray(parsed)) {
      saveSystems([DEFAULT_RANKING_SYSTEM]);
      return [DEFAULT_RANKING_SYSTEM];
    }

    const { systems, changed } = migrateSystems(parsed);
    if (changed) saveSystems(systems);
    return systems;
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

import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a number as Korean Won */
export function formatKRW(value: number): string {
  if (value >= 1_000_000_000_000) {
    return `${(value / 1_000_000_000_000).toFixed(1)}조`;
  }
  if (value >= 100_000_000) {
    return `${(value / 100_000_000).toFixed(0)}억`;
  }
  if (value >= 10_000) {
    return `${(value / 10_000).toFixed(0)}만`;
  }
  return value.toLocaleString("ko-KR");
}

/** Format a number as percentage */
export function formatPercent(value: number, decimals = 1): string {
  return `${(value * 100).toFixed(decimals)}%`;
}

/** Format a number with fixed decimals */
export function formatNumber(value: number, decimals = 2): string {
  return value.toFixed(decimals);
}

/** Color for a percentile score (0-100) */
export function scoreColor(score: number): string {
  if (score >= 80) return "text-green-600 dark:text-green-400";
  if (score >= 60) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 40) return "text-yellow-600 dark:text-yellow-400";
  if (score >= 20) return "text-orange-600 dark:text-orange-400";
  return "text-red-600 dark:text-red-400";
}

/** Background color for a percentile score */
export function scoreBg(score: number): string {
  if (score >= 80) return "bg-green-100 dark:bg-green-900/30";
  if (score >= 60) return "bg-emerald-100 dark:bg-emerald-900/30";
  if (score >= 40) return "bg-yellow-100 dark:bg-yellow-900/30";
  if (score >= 20) return "bg-orange-100 dark:bg-orange-900/30";
  return "bg-red-100 dark:bg-red-900/30";
}

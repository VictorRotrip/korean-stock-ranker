"use client";

import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { Plus, Copy, Trash2, Pencil, Play, Download, Upload, Undo2, X } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { RankingSystem } from "@/types";
import { getSavedSystems, deleteSystem, duplicateSystem, upsertSystem } from "@/lib/store";
import { collectFactorIds } from "@/lib/ranking-engine";

export default function RankingSystemsPage() {
  const [systems, setSystems] = useState<RankingSystem[]>([]);
  const [recentlyDeleted, setRecentlyDeleted] = useState<RankingSystem | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const undoTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSystems(getSavedSystems());
    return () => { if (undoTimer.current) clearTimeout(undoTimer.current); };
  }, []);

  const refresh = () => setSystems(getSavedSystems());

  const handleDelete = (id: string) => {
    const sys = systems.find(s => s.id === id) ?? null;
    deleteSystem(id);
    refresh();
    // Soft-delete UX: keep the deleted system around so it can be restored.
    setRecentlyDeleted(sys);
    if (undoTimer.current) clearTimeout(undoTimer.current);
    undoTimer.current = setTimeout(() => setRecentlyDeleted(null), 8000);
  };

  const handleUndo = () => {
    if (recentlyDeleted) {
      upsertSystem(recentlyDeleted);
      refresh();
    }
    setRecentlyDeleted(null);
    if (undoTimer.current) clearTimeout(undoTimer.current);
  };

  const handleDuplicate = (id: string) => {
    duplicateSystem(id);
    refresh();
  };

  const handleExport = (system: RankingSystem) => {
    const blob = new Blob([JSON.stringify(system, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const safe = system.name.replace(/[^\w.-]+/g, "_").slice(0, 60) || "ranking-system";
    a.download = `${safe}.ranking.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImportFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    setImportError(null);
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result)) as RankingSystem;
        if (!parsed || !parsed.tree || !parsed.name) {
          setImportError("That file isn't a valid ranking system.");
          return;
        }
        // Always import as a NEW system so it never overwrites an existing one.
        const imported: RankingSystem = {
          ...parsed,
          id: crypto.randomUUID(),
          name: parsed.name,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        };
        upsertSystem(imported);
        refresh();
      } catch {
        setImportError("Could not read that file — is it a ranking-system JSON export?");
      }
    };
    reader.readAsText(file);
    e.target.value = "";  // allow re-importing the same file
  };

  const handleCreateNew = () => {
    const newSystem: RankingSystem = {
      id: crypto.randomUUID(),
      name: "New Ranking System",
      description: "A new custom ranking system",
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      tree: {
        id: "root",
        type: "composite",
        name: "Composite",
        weight: 100,
        children: [],
      },
      options: {
        missingValueHandling: "median",
        winsorize: false,
        useZScore: false,
        sectorNeutral: false,
        industryNeutral: false,
      },
    };
    window.location.href = `/ranking-systems/${newSystem.id}?new=1`;
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Ranking Systems</h1>
          <p className="text-muted-foreground mt-1">
            Build and manage multi-factor ranking models
          </p>
        </div>
        <div className="flex gap-2">
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            className="hidden"
            onChange={handleImportFile}
          />
          <Button variant="outline" onClick={() => fileInput.current?.click()}>
            <Upload className="h-4 w-4 mr-2" />
            Import
          </Button>
          <Button onClick={handleCreateNew}>
            <Plus className="h-4 w-4 mr-2" />
            New System
          </Button>
        </div>
      </div>

      {/* Undo banner after a delete */}
      {recentlyDeleted && (
        <div className="flex items-center justify-between rounded-md border bg-muted/50 px-4 py-2 text-sm">
          <span>
            Deleted <span className="font-medium">{recentlyDeleted.name}</span>.
          </span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleUndo}>
              <Undo2 className="h-3.5 w-3.5 mr-1.5" />
              Undo
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setRecentlyDeleted(null)}>
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}

      {importError && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {importError}
        </div>
      )}

      {systems.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <p className="text-muted-foreground mb-4">No ranking systems yet</p>
            <Button onClick={handleCreateNew}>
              <Plus className="h-4 w-4 mr-2" />
              Create Your First System
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {systems.map((system) => {
            const factorCount = collectFactorIds(system.tree).length;
            const categoryCount = system.tree.children?.length ?? 0;

            return (
              <Card key={system.id} className="flex flex-col">
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1 min-w-0">
                      <CardTitle className="text-base truncate">{system.name}</CardTitle>
                      <CardDescription className="mt-1 line-clamp-2">
                        {system.description || "No description"}
                      </CardDescription>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="flex-1 space-y-3">
                  <div className="flex flex-wrap gap-1.5">
                    {system.tree.children?.map((cat) => (
                      <Badge key={cat.id} variant="secondary" className="text-xs">
                        {cat.name}: {cat.weight}%
                      </Badge>
                    ))}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {factorCount} factors across {categoryCount} categories
                  </div>
                  <div className="flex gap-2 pt-2">
                    <Link href={`/ranking-systems/${system.id}`} className="flex-1">
                      <Button variant="outline" size="sm" className="w-full">
                        <Pencil className="h-3 w-3 mr-1.5" />
                        Edit
                      </Button>
                    </Link>
                    <Link href={`/ranking-systems/${system.id}/results`} className="flex-1">
                      <Button size="sm" className="w-full">
                        <Play className="h-3 w-3 mr-1.5" />
                        Run
                      </Button>
                    </Link>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9"
                      title="Export to file"
                      onClick={() => handleExport(system)}
                    >
                      <Download className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9"
                      title="Duplicate"
                      onClick={() => handleDuplicate(system.id)}
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 text-destructive hover:text-destructive"
                      title="Delete"
                      onClick={() => handleDelete(system.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

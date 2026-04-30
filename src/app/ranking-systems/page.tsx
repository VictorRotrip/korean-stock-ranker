"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { Plus, Copy, Trash2, Pencil, Play } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { RankingSystem } from "@/types";
import { getSavedSystems, deleteSystem, duplicateSystem } from "@/lib/store";
import { collectFactorIds } from "@/lib/ranking-engine";

export default function RankingSystemsPage() {
  const [systems, setSystems] = useState<RankingSystem[]>([]);

  useEffect(() => {
    setSystems(getSavedSystems());
  }, []);

  const handleDelete = (id: string) => {
    if (confirm("Delete this ranking system?")) {
      deleteSystem(id);
      setSystems(getSavedSystems());
    }
  };

  const handleDuplicate = (id: string) => {
    duplicateSystem(id);
    setSystems(getSavedSystems());
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
    // Navigate to the builder
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
        <Button onClick={handleCreateNew}>
          <Plus className="h-4 w-4 mr-2" />
          New System
        </Button>
      </div>

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
                      onClick={() => handleDuplicate(system.id)}
                    >
                      <Copy className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 text-destructive hover:text-destructive"
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

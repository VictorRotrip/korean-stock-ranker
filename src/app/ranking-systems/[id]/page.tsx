"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import {
  Plus, Trash2, Save, Play, ChevronRight, ChevronDown,
  GripVertical, ArrowUpDown, FolderPlus,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { RankingSystem, RankingNode, RankingOptions, FactorDefinition } from "@/types";
import { getSystemById, upsertSystem } from "@/lib/store";
import { DEFAULT_RANKING_SYSTEM } from "@/lib/ranking-engine";
import { getFactorDefinitions, getCategories, CATEGORY_LABELS, getFactorsByCategory } from "@/lib/factors";
import { cn, scoreColor } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Tree Node Component
// ---------------------------------------------------------------------------

function TreeNode({
  node,
  depth,
  onUpdate,
  onDelete,
  factorDefs,
}: {
  node: RankingNode;
  depth: number;
  onUpdate: (updated: RankingNode) => void;
  onDelete: () => void;
  factorDefs: FactorDefinition[];
}) {
  const [expanded, setExpanded] = useState(true);
  const hasChildren = node.children && node.children.length > 0;
  const isRoot = depth === 0;

  const handleWeightChange = (value: number[]) => {
    onUpdate({ ...node, weight: value[0] });
  };

  const handleNameChange = (name: string) => {
    onUpdate({ ...node, name });
  };

  const handleAddCategory = () => {
    const newChild: RankingNode = {
      id: crypto.randomUUID(),
      type: "category",
      name: "New Category",
      weight: 25,
      children: [],
    };
    onUpdate({
      ...node,
      children: [...(node.children ?? []), newChild],
    });
  };

  const handleAddFactor = (factorId: string) => {
    const def = factorDefs.find(f => f.id === factorId);
    if (!def) return;

    const newChild: RankingNode = {
      id: crypto.randomUUID(),
      type: "factor",
      name: def.name,
      weight: 25,
      factorId: factorId,
    };
    onUpdate({
      ...node,
      children: [...(node.children ?? []), newChild],
    });
  };

  const handleChildUpdate = (index: number, updated: RankingNode) => {
    const newChildren = [...(node.children ?? [])];
    newChildren[index] = updated;
    onUpdate({ ...node, children: newChildren });
  };

  const handleChildDelete = (index: number) => {
    const newChildren = (node.children ?? []).filter((_, i) => i !== index);
    onUpdate({ ...node, children: newChildren });
  };

  // Color coding by node type
  const borderColor = node.type === "composite"
    ? "border-l-blue-500"
    : node.type === "category"
    ? "border-l-purple-500"
    : "border-l-emerald-500";

  const typeLabel = node.type === "composite" ? "Composite" : node.type === "category" ? "Category" : "Factor";

  return (
    <div className={cn("border-l-2 rounded-md", borderColor, depth > 0 && "ml-4")}>
      <div className="flex items-center gap-2 p-3 bg-card hover:bg-accent/50 rounded-md transition-colors">
        {/* Expand/collapse toggle */}
        {(node.type === "composite" || node.type === "category") && (
          <button onClick={() => setExpanded(!expanded)} className="p-0.5">
            {expanded ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
          </button>
        )}

        {node.type === "factor" && <div className="w-5" />}

        {/* Node info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className={cn(
                "text-[10px] px-1.5 py-0",
                node.type === "composite" && "bg-blue-50 text-blue-700 border-blue-200",
                node.type === "category" && "bg-purple-50 text-purple-700 border-purple-200",
                node.type === "factor" && "bg-emerald-50 text-emerald-700 border-emerald-200",
              )}
            >
              {typeLabel}
            </Badge>
            {isRoot ? (
              <span className="text-sm font-medium">{node.name}</span>
            ) : (
              <Input
                value={node.name}
                onChange={(e) => handleNameChange(e.target.value)}
                className="h-7 text-sm border-none bg-transparent p-0 focus-visible:ring-0 font-medium"
              />
            )}
          </div>
          {node.type === "factor" && node.factorId && (
            <span className="text-xs text-muted-foreground ml-14">
              {factorDefs.find(f => f.id === node.factorId)?.description?.substring(0, 60)}...
            </span>
          )}
        </div>

        {/* Weight slider */}
        {!isRoot && (
          <div className="flex items-center gap-3 min-w-[180px]">
            <Slider
              value={[node.weight]}
              onValueChange={handleWeightChange}
              min={0}
              max={100}
              step={5}
              className="w-24"
            />
            <span className="text-sm font-mono w-10 text-right">{node.weight}%</span>
          </div>
        )}

        {/* Delete button */}
        {!isRoot && (
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onDelete}>
            <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
          </Button>
        )}
      </div>

      {/* Children */}
      {expanded && hasChildren && (
        <div className="space-y-1 pb-2 pl-2">
          {node.children!.map((child, index) => (
            <TreeNode
              key={child.id}
              node={child}
              depth={depth + 1}
              onUpdate={(updated) => handleChildUpdate(index, updated)}
              onDelete={() => handleChildDelete(index)}
              factorDefs={factorDefs}
            />
          ))}
        </div>
      )}

      {/* Add buttons */}
      {expanded && (node.type === "composite" || node.type === "category") && (
        <div className="flex gap-2 pl-8 pb-3">
          {node.type === "composite" && (
            <Button variant="outline" size="sm" onClick={handleAddCategory} className="text-xs h-7">
              <FolderPlus className="h-3 w-3 mr-1" />
              Add Category
            </Button>
          )}
          <FactorPicker
            factorDefs={factorDefs}
            onSelect={handleAddFactor}
            existingFactorIds={collectExistingFactorIds(node)}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Factor Picker
// ---------------------------------------------------------------------------

function FactorPicker({
  factorDefs,
  onSelect,
  existingFactorIds,
}: {
  factorDefs: FactorDefinition[];
  onSelect: (factorId: string) => void;
  existingFactorIds: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const categories = getCategories();

  if (!open) {
    return (
      <Button variant="outline" size="sm" onClick={() => setOpen(true)} className="text-xs h-7">
        <Plus className="h-3 w-3 mr-1" />
        Add Factor
      </Button>
    );
  }

  return (
    <div className="border rounded-md bg-card p-3 space-y-2 min-w-[300px]">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium">Select a Factor</span>
        <Button variant="ghost" size="sm" onClick={() => setOpen(false)} className="h-6 text-xs">
          Cancel
        </Button>
      </div>
      <div className="max-h-60 overflow-y-auto space-y-2">
        {categories.map(cat => {
          const catFactors = factorDefs.filter(f => f.category === cat);
          if (catFactors.length === 0) return null;

          return (
            <div key={cat}>
              <div className="text-xs font-medium text-muted-foreground mb-1">
                {CATEGORY_LABELS[cat]}
              </div>
              {catFactors.map(f => (
                <button
                  key={f.id}
                  disabled={existingFactorIds.has(f.id)}
                  onClick={() => { onSelect(f.id); setOpen(false); }}
                  className={cn(
                    "w-full text-left px-2 py-1.5 rounded text-xs hover:bg-accent transition-colors",
                    existingFactorIds.has(f.id) && "opacity-40 cursor-not-allowed"
                  )}
                >
                  <div className="font-medium">{f.name}</div>
                  <div className="text-muted-foreground text-[10px]">
                    {f.direction === "higher_is_better" ? "Higher is better" : "Lower is better"}
                  </div>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper: collect all factorIds in a tree
// ---------------------------------------------------------------------------

function collectExistingFactorIds(node: RankingNode): Set<string> {
  const ids = new Set<string>();
  if (node.factorId) ids.add(node.factorId);
  if (node.children) {
    for (const child of node.children) {
      for (const id of collectExistingFactorIds(child)) {
        ids.add(id);
      }
    }
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Main Builder Page
// ---------------------------------------------------------------------------

export default function RankingSystemBuilderPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [system, setSystem] = useState<RankingSystem | null>(null);
  const [saved, setSaved] = useState(false);
  const factorDefs = getFactorDefinitions();

  useEffect(() => {
    const loaded = getSystemById(id);
    if (loaded) {
      setSystem(loaded);
    } else {
      // New system
      setSystem({
        id,
        name: "New Ranking System",
        description: "",
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
      });
    }
  }, [id]);

  const handleSave = useCallback(() => {
    if (!system) return;
    upsertSystem(system);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }, [system]);

  const handleRunRanking = () => {
    handleSave();
    router.push(`/ranking-systems/${id}/results`);
  };

  if (!system) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex-1 space-y-1">
          <Input
            value={system.name}
            onChange={(e) => setSystem({ ...system, name: e.target.value })}
            className="text-2xl font-bold border-none bg-transparent p-0 h-auto focus-visible:ring-0"
          />
          <Input
            value={system.description ?? ""}
            onChange={(e) => setSystem({ ...system, description: e.target.value })}
            placeholder="Add a description..."
            className="text-sm text-muted-foreground border-none bg-transparent p-0 h-auto focus-visible:ring-0"
          />
        </div>
        <div className="flex gap-2">
          <Button variant="outline" onClick={handleSave}>
            <Save className="h-4 w-4 mr-2" />
            {saved ? "Saved!" : "Save"}
          </Button>
          <Button onClick={handleRunRanking}>
            <Play className="h-4 w-4 mr-2" />
            Run Ranking
          </Button>
        </div>
      </div>

      <Tabs defaultValue="tree">
        <TabsList>
          <TabsTrigger value="tree">Ranking Tree</TabsTrigger>
          <TabsTrigger value="options">Options</TabsTrigger>
        </TabsList>

        {/* Tree Editor */}
        <TabsContent value="tree">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Factor Tree</CardTitle>
              <p className="text-xs text-muted-foreground">
                Build your ranking model by adding categories and factors. Adjust weights so siblings sum to 100%.
              </p>
            </CardHeader>
            <CardContent>
              <TreeNode
                node={system.tree}
                depth={0}
                onUpdate={(updated) => setSystem({ ...system, tree: updated })}
                onDelete={() => {}}
                factorDefs={factorDefs}
              />
            </CardContent>
          </Card>

          {/* Weight summary */}
          {system.tree.children && system.tree.children.length > 0 && (
            <Card className="mt-4">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Weight Summary</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex gap-1 h-6 rounded overflow-hidden">
                  {system.tree.children.map((child, i) => {
                    const colors = [
                      "bg-blue-500", "bg-purple-500", "bg-emerald-500",
                      "bg-amber-500", "bg-rose-500", "bg-cyan-500",
                    ];
                    return (
                      <div
                        key={child.id}
                        className={cn("flex items-center justify-center text-white text-xs font-medium", colors[i % colors.length])}
                        style={{ width: `${child.weight}%` }}
                      >
                        {child.weight >= 10 ? `${child.name} ${child.weight}%` : ""}
                      </div>
                    );
                  })}
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  Total: {system.tree.children.reduce((sum, c) => sum + c.weight, 0)}%
                  {system.tree.children.reduce((sum, c) => sum + c.weight, 0) !== 100 && (
                    <span className="text-amber-600 ml-2">(should be 100%)</span>
                  )}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Options */}
        <TabsContent value="options">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Ranking Options</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Missing value handling */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Missing Value Handling</p>
                  <p className="text-xs text-muted-foreground">
                    How to treat stocks with missing factor data
                  </p>
                </div>
                <Select
                  value={system.options.missingValueHandling}
                  onValueChange={(v) =>
                    setSystem({
                      ...system,
                      options: { ...system.options, missingValueHandling: v as RankingOptions["missingValueHandling"] },
                    })
                  }
                >
                  <SelectTrigger className="w-40">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="exclude">Exclude</SelectItem>
                    <SelectItem value="median">Assign Median</SelectItem>
                    <SelectItem value="worst">Assign Worst</SelectItem>
                    <SelectItem value="neutral">Assign Neutral (50)</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {/* Winsorization */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Winsorization</p>
                  <p className="text-xs text-muted-foreground">
                    Clip extreme values at 5th/95th percentile before ranking
                  </p>
                </div>
                <Switch
                  checked={system.options.winsorize}
                  onCheckedChange={(checked) =>
                    setSystem({
                      ...system,
                      options: { ...system.options, winsorize: checked, winsorizePercentile: 0.05 },
                    })
                  }
                />
              </div>

              {/* Sector Neutral */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Sector-Neutral Ranking</p>
                  <p className="text-xs text-muted-foreground">
                    Rank factors within each sector independently
                  </p>
                </div>
                <Switch
                  checked={system.options.sectorNeutral}
                  onCheckedChange={(checked) =>
                    setSystem({
                      ...system,
                      options: { ...system.options, sectorNeutral: checked },
                    })
                  }
                />
              </div>

              {/* Z-Score */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">Z-Score Normalization</p>
                  <p className="text-xs text-muted-foreground">
                    Use z-scores instead of percentile ranks (experimental)
                  </p>
                </div>
                <Switch
                  checked={system.options.useZScore}
                  onCheckedChange={(checked) =>
                    setSystem({
                      ...system,
                      options: { ...system.options, useZScore: checked },
                    })
                  }
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

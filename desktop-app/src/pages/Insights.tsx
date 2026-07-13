import { useCallback, useEffect, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { getInsightsGraph } from "../lib/hermes-bridge";
import type { GraphNode, InsightsGraph } from "../lib/hermes-bridge";

// ── Colour palette per node type ──────────────────────────────────────────

const NODE_COLORS: Record<GraphNode["type"], string> = {
  track:        "#a78bfa", // violet
  mood:         "#f472b6", // pink
  element:      "#34d399", // emerald
  key:          "#60a5fa", // blue
  bpm:          "#fb923c", // orange
  section:      "#facc15", // yellow
  genre:        "#e879f9", // fuchsia
  instrument:   "#2dd4bf", // teal
  subgenre:     "#c084fc", // purple-light
  agent:        "#f87171", // red
  verdict:      "#4ade80", // green
  mode:         "#818cf8", // indigo
  energy_level: "#fbbf24", // amber
  rhythm_feel:  "#38bdf8", // sky
  texture:      "#86efac", // green-light
};

const NODE_RADIUS: Record<GraphNode["type"], number> = {
  track:        10,
  mood:         7,
  element:      6,
  key:          8,
  bpm:          7,
  section:      6,
  genre:        8,
  instrument:   6,
  subgenre:     7,
  agent:        9,
  verdict:      9,
  mode:         7,
  energy_level: 6,
  rhythm_feel:  6,
  texture:      6,
};

type FilterKey = GraphNode["type"];
const ALL_FILTERS: FilterKey[] = [
  "track", "genre", "subgenre", "mood", "section",
  "key", "bpm", "element", "instrument", "agent", "verdict",
  "mode", "energy_level", "rhythm_feel", "texture",
];
const FILTER_LABELS: Record<FilterKey, string> = {
  track:        "Tracks",
  genre:        "Genre",
  subgenre:     "Subgenre",
  mood:         "Moods",
  section:      "Sections",
  key:          "Keys",
  bpm:          "BPM",
  element:      "Elements",
  instrument:   "Instruments",
  agent:        "Agents",
  verdict:      "Verdict",
  mode:         "Mode",
  energy_level: "Energy",
  rhythm_feel:  "Rhythm Feel",
  texture:      "Texture",
};

// ── Graph data shape for react-force-graph-2d ────────────────────────────

interface FGNode extends GraphNode {
  x?: number;
  y?: number;
}

interface FGLink {
  source: string | FGNode;
  target: string | FGNode;
}

function toFGData(
  graph: InsightsGraph,
  visible: Set<FilterKey>,
): { nodes: FGNode[]; links: FGLink[] } {
  const visibleIds = new Set(
    graph.nodes.filter((n) => visible.has(n.type)).map((n) => n.id),
  );
  const nodes: FGNode[] = graph.nodes.filter((n) => visible.has(n.type));
  const links: FGLink[] = graph.links.filter(
    (l) => visibleIds.has(l.source) && visibleIds.has(l.target),
  );
  return { nodes, links };
}

// ── Component ─────────────────────────────────────────────────────────────

export default function Insights() {
  const [graph, setGraph] = useState<InsightsGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [visible, setVisible] = useState<Set<FilterKey>>(new Set(ALL_FILTERS));
  const [hovered, setHovered] = useState<FGNode | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dims, setDims] = useState({ width: 800, height: 600 });

  useEffect(() => {
    getInsightsGraph()
      .then(setGraph)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, []);

  // Observe container size for responsive canvas
  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDims({ width: Math.floor(width), height: Math.floor(height) });
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const toggleFilter = useCallback((type: FilterKey) => {
    setVisible((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        // Keep at least one filter active
        if (next.size > 1) next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }, []);

  const fgData = graph ? toFGData(graph, visible) : { nodes: [], links: [] };

  const nodeCanvasObject = useCallback(
    (node: FGNode, ctx: CanvasRenderingContext2D) => {
      const r = NODE_RADIUS[node.type] ?? 6;
      const color = NODE_COLORS[node.type] ?? "#ffffff";
      const isHovered = hovered?.id === node.id;

      ctx.beginPath();
      ctx.arc(node.x ?? 0, node.y ?? 0, isHovered ? r + 2 : r, 0, 2 * Math.PI);
      ctx.fillStyle = isHovered ? "#fff" : color;
      ctx.fill();
      if (isHovered) {
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Label — only for tracks and hovered nodes
      if (node.type === "track" || isHovered) {
        const fontSize = node.type === "track" ? 11 : 10;
        ctx.font = `${fontSize}px Inter, sans-serif`;
        ctx.fillStyle = "#e4e4e7";
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillText(node.label, node.x ?? 0, (node.y ?? 0) + r + 3);
      }
    },
    [hovered],
  );

  const stats = graph
    ? {
        tracks:  graph.nodes.filter((n) => n.type === "track").length,
        genres:  graph.nodes.filter((n) => n.type === "genre").length,
        moods:   graph.nodes.filter((n) => n.type === "mood").length,
        keys:    graph.nodes.filter((n) => n.type === "key").length,
        agents:  graph.nodes.filter((n) => n.type === "agent").length,
      }
    : null;

  return (
    <div className="flex h-full min-h-0 bg-surface-0 text-zinc-100">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 border-r border-surface-3 flex flex-col gap-6 p-4 overflow-y-auto">
        <div>
          <h2 className="text-xs font-semibold text-zinc-500 uppercase tracking-widest mb-3">
            Knowledge Graph
          </h2>
          {stats && (
            <div className="flex flex-col gap-1 text-xs text-zinc-400 mb-4">
              <span>{stats.tracks} track{stats.tracks !== 1 ? "s" : ""}</span>
              <span>{stats.genres} genre{stats.genres !== 1 ? "s" : ""}</span>
              <span>{stats.moods} mood{stats.moods !== 1 ? "s" : ""}</span>
              <span>{stats.keys} key{stats.keys !== 1 ? "s" : ""}</span>
              <span>{stats.agents} agent{stats.agents !== 1 ? "s" : ""}</span>
            </div>
          )}
        </div>

        <div>
          <p className="text-xs font-medium text-zinc-500 mb-2">Show / hide</p>
          <div className="flex flex-col gap-1">
            {ALL_FILTERS.map((type) => (
              <button
                key={type}
                onClick={() => toggleFilter(type)}
                className={`flex items-center gap-2 px-2 py-1.5 rounded text-xs transition-colors ${
                  visible.has(type)
                    ? "bg-surface-2 text-zinc-100"
                    : "text-zinc-600 hover:text-zinc-400"
                }`}
              >
                <span
                  className="w-2.5 h-2.5 rounded-full shrink-0"
                  style={{ background: NODE_COLORS[type] }}
                />
                {FILTER_LABELS[type]}
              </button>
            ))}
          </div>
        </div>

        {/* Legend */}
        <div className="mt-auto">
          <p className="text-xs font-medium text-zinc-500 mb-2">Legend</p>
          <div className="flex flex-col gap-1.5">
            {ALL_FILTERS.map((type) => (
              <div key={type} className="flex items-center gap-2 text-xs text-zinc-500">
                <span
                  className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: NODE_COLORS[type] }}
                />
                {FILTER_LABELS[type]}
              </div>
            ))}
          </div>
        </div>
      </aside>

      {/* Graph canvas */}
      <div className="flex-1 min-w-0 relative" ref={containerRef}>
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-zinc-500 text-sm">Building graph…</span>
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-red-400 text-sm px-6 text-center">{error}</span>
          </div>
        )}
        {!loading && !error && graph && fgData.nodes.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-zinc-500 text-sm">
              No tracks with segment analysis yet. Drop a track to get started.
            </span>
          </div>
        )}
        {!loading && !error && fgData.nodes.length > 0 && (
          <ForceGraph2D
            width={dims.width}
            height={dims.height}
            graphData={fgData}
            nodeId="id"
            nodeCanvasObject={nodeCanvasObject}
            nodeCanvasObjectMode={() => "replace"}
            nodePointerAreaPaint={(node: FGNode, color, ctx) => {
              const r = (NODE_RADIUS[node.type] ?? 6) + 4;
              ctx.beginPath();
              ctx.arc(node.x ?? 0, node.y ?? 0, r, 0, 2 * Math.PI);
              ctx.fillStyle = color;
              ctx.fill();
            }}
            onNodeHover={(node) => setHovered((node as FGNode | null) ?? null)}
            linkColor={() => "rgba(255,255,255,0.08)"}
            linkWidth={1}
            backgroundColor="#09090b"
            cooldownTicks={120}
            d3AlphaDecay={0.02}
            d3VelocityDecay={0.3}
          />
        )}

        {/* Tooltip */}
        {hovered && (
          <div className="absolute top-4 right-4 bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-xs pointer-events-none max-w-48">
            <p className="text-zinc-400 uppercase tracking-widest text-[10px] mb-1">
              {hovered.type}
            </p>
            <p className="text-zinc-100 font-medium break-words">{hovered.label}</p>
          </div>
        )}
      </div>
    </div>
  );
}

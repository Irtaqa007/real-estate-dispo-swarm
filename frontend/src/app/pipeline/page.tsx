"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import {
  Building2,
  Send,
  MessageCircle,
  Handshake,
  FileCheck,
  CheckCircle2,
  Clock,
  TrendingUp,
  DollarSign,
  Users,
  MapPin,
  X,
  AlertCircle,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PipelineDeal {
  deal_id: string;
  address: string;
  city: string | null;
  state: string | null;
  property_type: string;
  asking_price: number;
  arv: number;
  status: string;
  campaigns_total: number;
  campaigns_sent: number;
  campaigns_replied: number;
  campaigns_passed: number;
  campaigns_contract: number;
  stage: string;
  created_at: string | null;
  last_activity_at: string | null;
}

// ---------------------------------------------------------------------------
// Column definitions
// ---------------------------------------------------------------------------

interface ColumnDef {
  id: string;
  label: string;
  icon: React.ReactNode;
  color: string;
  bgGradient: string;
}

const COLUMNS: ColumnDef[] = [
  {
    id: "Available",
    label: "Available",
    icon: <Building2 className="w-4 h-4" />,
    color: "text-emerald-400",
    bgGradient: "from-emerald-500/5 via-transparent to-transparent",
  },
  {
    id: "Launched",
    label: "Launched",
    icon: <Send className="w-4 h-4" />,
    color: "text-blue-400",
    bgGradient: "from-blue-500/5 via-transparent to-transparent",
  },
  {
    id: "Replied",
    label: "Replied",
    icon: <MessageCircle className="w-4 h-4" />,
    color: "text-cyan-400",
    bgGradient: "from-cyan-500/5 via-transparent to-transparent",
  },
  {
    id: "Negotiating",
    label: "Negotiating",
    icon: <Handshake className="w-4 h-4" />,
    color: "text-amber-400",
    bgGradient: "from-amber-500/5 via-transparent to-transparent",
  },
  {
    id: "Contract Ready",
    label: "Contract Ready",
    icon: <FileCheck className="w-4 h-4" />,
    color: "text-sky-400",
    bgGradient: "from-sky-500/5 via-transparent to-transparent",
  },
  {
    id: "Closed",
    label: "Closed",
    icon: <CheckCircle2 className="w-4 h-4" />,
    color: "text-purple-400",
    bgGradient: "from-purple-500/5 via-transparent to-transparent",
  },
];

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function formatCurrency(val: number | null | undefined): string {
  if (val == null) return "$0";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(val);
}

function daysSince(dateStr: string | null): number | null {
  if (!dateStr) return null;
  const created = new Date(dateStr);
  const now = new Date();
  const diff = now.getTime() - created.getTime();
  return Math.floor(diff / (1000 * 60 * 60 * 24));
}

function formatDays(days: number | null): string {
  if (days === null) return "—";
  if (days === 0) return "Today";
  if (days === 1) return "1 day";
  return `${days} days`;
}

// ---------------------------------------------------------------------------
// Deal Card
// ---------------------------------------------------------------------------

function DealCard({ deal }: { deal: PipelineDeal }) {
  const days = daysSince(deal.created_at);
  const activeConversations = deal.campaigns_replied - deal.campaigns_passed - deal.campaigns_contract;

  return (
    <Link
      href={`/deals/${deal.deal_id}`}
      className="block group rounded-xl border border-slate-700/50 bg-slate-800/40 p-4 space-y-3
                 transition-all duration-200 hover:border-slate-600/50 hover:bg-slate-800/70
                 hover:shadow-lg hover:-translate-y-0.5 active:translate-y-0"
    >
      {/* Address & type */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-white truncate group-hover:text-blue-300 transition-colors">
            {deal.address}
          </p>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
              deal.property_type === "House"
                ? "bg-blue-500/10 text-blue-400"
                : "bg-emerald-500/10 text-emerald-400"
            }`}>
              {deal.property_type === "House" ? "H" : "L"}
            </span>
            {deal.city && (
              <span className="flex items-center gap-1 text-[10px] text-slate-500">
                <MapPin className="w-2.5 h-2.5" />
                {deal.city}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Key metrics */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-slate-900/60 rounded-lg p-2">
          <p className="text-[10px] text-slate-500 font-medium">Asking</p>
          <p className="text-sm font-bold text-white tabular-nums">
            {formatCurrency(deal.asking_price)}
          </p>
        </div>
        <div className="bg-slate-900/60 rounded-lg p-2">
          <p className="text-[10px] text-slate-500 font-medium">ARV</p>
          <p className="text-sm font-bold text-emerald-400 tabular-nums">
            {formatCurrency(deal.arv)}
          </p>
        </div>
      </div>

      {/* Footer stats */}
      <div className="flex items-center justify-between pt-1 border-t border-slate-700/30">
        <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
          <Clock className="w-3 h-3" />
          <span>{formatDays(days)}</span>
        </div>
        {deal.campaigns_total > 0 && (
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
            <Users className="w-3 h-3" />
            <span>
              {Math.max(0, activeConversations)} active
            </span>
          </div>
        )}
        {deal.campaigns_total > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-slate-500">
            <Send className="w-3 h-3" />
            <span>{deal.campaigns_sent}/{deal.campaigns_total}</span>
          </div>
        )}
      </div>
    </Link>
  );
}

// ---------------------------------------------------------------------------
// Column
// ---------------------------------------------------------------------------

function PipelineColumn({
  column,
  deals,
}: {
  column: ColumnDef;
  deals: PipelineDeal[];
}) {
  return (
    <div className="flex flex-col min-w-[280px] w-[280px] shrink-0">
      {/* Column header */}
      <div className={`flex items-center gap-2 px-3 py-2.5 rounded-t-xl bg-gradient-to-b ${column.bgGradient} border-b border-slate-700/30`}>
        <span className={column.color}>{column.icon}</span>
        <span className={`text-sm font-semibold ${column.color}`}>
          {column.label}
        </span>
        <span className="ml-auto text-xs font-medium text-slate-500 bg-slate-800/80 px-2 py-0.5 rounded-full tabular-nums">
          {deals.length}
        </span>
      </div>

      {/* Deal cards */}
      <div className="flex-1 space-y-3 p-3 overflow-y-auto bg-slate-900/30 rounded-b-xl border-x border-b border-slate-800/30 min-h-[200px]">
        {deals.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <div className="w-8 h-8 rounded-full bg-slate-800/50 flex items-center justify-center mb-2">
              <span className={`text-xs ${column.color}/50`}>{column.icon}</span>
            </div>
            <p className="text-[10px] text-slate-600">No deals</p>
          </div>
        ) : (
          deals.map((deal) => (
            <DealCard key={deal.deal_id} deal={deal} />
          ))
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function PipelinePage() {
  const [deals, setDeals] = useState<PipelineDeal[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<PipelineDeal[]>("/api/deals/pipeline");
      setDeals(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Auto-refresh every 30s
  usePolling(loadData, 30000);

  // Group deals by stage
  const grouped = COLUMNS.reduce(
    (acc, col) => {
      acc[col.id] = deals.filter((d) => d.stage === col.id);
      return acc;
    },
    {} as Record<string, PipelineDeal[]>,
  );

  // Total counts for header
  const totalDeals = deals.length;
  const contractReady = deals.filter((d) => d.stage === "Contract Ready").length;
  const activeNegotiations = deals.filter((d) => d.stage === "Negotiating").length;

  if (loading && deals.length === 0) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 animate-pulse mx-auto" />
          <p className="text-sm text-slate-500 animate-pulse">Loading pipeline...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950 flex flex-col">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-full mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Pipeline</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {totalDeals} deals · {activeNegotiations} negotiating · {contractReady} contract ready
            </p>
          </div>
          <button
            onClick={loadData}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-700/50 transition-colors"
          >
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M1 4v6h6M23 20v-6h-6" />
              <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15" />
            </svg>
            Refresh
          </button>
        </div>
      </header>

      {/* Error banner */}
      {error && (
        <div className="max-w-full mx-auto px-6 pt-4">
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-auto hover:text-red-300 transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Kanban board */}
      <main className="flex-1 overflow-x-auto px-6 py-6">
        <div className="flex gap-4 min-h-[calc(100vh-180px)]">
          {COLUMNS.map((col) => (
            <PipelineColumn
              key={col.id}
              column={col}
              deals={grouped[col.id] || []}
            />
          ))}
        </div>
      </main>
    </div>
  );
}

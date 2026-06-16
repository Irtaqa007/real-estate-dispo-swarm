"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Search,
  Eye,
  X,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  ChevronDown,
  Activity,
  RefreshCw,
  Mail,
  DollarSign,
  Users,
  Building2,
  Handshake,
  Clock,
  MessageSquare,
  Ban,
  CheckCircle2,
  Zap,
  Info,
  Loader2,
  Filter,
  UserCheck,
  TrendingUp,
  Shield,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ActivityEntry {
  id: string;
  entity_type: string | null;
  entity_id: string | null;
  action: string | null;
  metadata: Record<string, any>;
  created_at: string | null;
}

interface ActivityResponse {
  items: ActivityEntry[];
  total: number;
  page: number;
  per_page: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const entityConfig: Record<string, { label: string; icon: React.ReactNode; color: string }> = {
  deal: {
    label: "Deal",
    icon: <Building2 className="w-3.5 h-3.5" />,
    color: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  },
  campaign: {
    label: "Campaign",
    icon: <Mail className="w-3.5 h-3.5" />,
    color: "bg-purple-500/10 text-purple-400 border-purple-500/20",
  },
  buyer: {
    label: "Buyer",
    icon: <Users className="w-3.5 h-3.5" />,
    color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/20",
  },
  jv: {
    label: "JV Partner",
    icon: <Handshake className="w-3.5 h-3.5" />,
    color: "bg-cyan-500/10 text-cyan-400 border-cyan-500/20",
  },
};

const actionIcons: Record<string, React.ReactNode> = {
  email_sent: <Mail className="w-3.5 h-3.5" />,
  email_failed: <Ban className="w-3.5 h-3.5" />,
  reply_received: <MessageSquare className="w-3.5 h-3.5" />,
  closed: <DollarSign className="w-3.5 h-3.5" />,
  status_change: <TrendingUp className="w-3.5 h-3.5" />,
  profile_updated: <UserCheck className="w-3.5 h-3.5" />,
  question_auto_answer: <Zap className="w-3.5 h-3.5" />,
  question_final_answer: <Zap className="w-3.5 h-3.5" />,
  question_escalated: <AlertCircle className="w-3.5 h-3.5" />,
};

const actionLabels: Record<string, string> = {
  email_sent: "Email Sent",
  email_failed: "Email Failed",
  reply_received: "Reply Received",
  closed: "Deal Closed",
  status_change: "Status Change",
  profile_updated: "Profile Updated",
  question_auto_answer: "Auto-Answer Sent",
  question_final_answer: "Final Answer Sent",
  question_escalated: "Escalated",
  created: "Created",
  updated: "Updated",
  deleted: "Deleted",
};

function getActionLabel(action: string | null): string {
  if (!action) return "Unknown";
  return actionLabels[action] || action.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function getActionIcon(action: string | null): React.ReactNode {
  if (!action) return <Activity className="w-3.5 h-3.5" />;
  return actionIcons[action] || <Activity className="w-3.5 h-3.5" />;
}

function formatDate(iso: string | null) {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return "Just now";
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;

  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatDateFull(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function truncate(text: string | null, max: number) {
  if (!text) return "";
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}

function formatCurrency(val: number | null | undefined): string {
  if (val == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(val);
}

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Modal wrapper
// ---------------------------------------------------------------------------

function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (open) {
      const handler = (e: KeyboardEvent) => {
        if (e.key === "Escape") onClose();
      };
      document.addEventListener("keydown", handler);
      return () => document.removeEventListener("keydown", handler);
    }
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-slate-700/50 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700/50">
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <button
            onClick={onClose}
            className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="px-6 py-4">{children}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity detail modal
// ---------------------------------------------------------------------------

function ActivityDetailModal({
  open,
  onClose,
  entry,
}: {
  open: boolean;
  onClose: () => void;
  entry: ActivityEntry | null;
}) {
  if (!entry) return null;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start gap-4 py-2.5 border-b border-slate-700/30 last:border-0">
      <span className="w-28 shrink-0 text-sm text-slate-500">{label}</span>
      <span className="text-sm text-white break-words">{value}</span>
    </div>
  );

  const cf = entityConfig[entry.entity_type || ""];
  const actionIcon = getActionIcon(entry.action);
  const actionLabel = getActionLabel(entry.action);

  // Build a readable summary of metadata
  const meta = entry.metadata || {};

  function renderMetadataValue(value: any): string {
    if (value === null || value === undefined) return "—";
    if (typeof value === "object") return JSON.stringify(value, null, 2);
    return String(value);
  }

  return (
    <Modal open={open} onClose={onClose} title="Activity Details">
      <div className="space-y-4">
        {/* Summary */}
        <div className="flex items-center gap-3 p-3 rounded-lg bg-slate-800/50 border border-slate-700/30">
          <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${cf ? cf.color : "bg-slate-700/50 text-slate-400"} border`}>
            {cf ? cf.icon : <Activity className="w-4 h-4" />}
          </div>
          <div>
            <p className="text-sm font-medium text-white">
              {cf ? cf.label : entry.entity_type || "System"}
            </p>
            <div className="flex items-center gap-1.5 text-xs text-slate-400 mt-0.5">
              <span className="inline-flex items-center gap-1">
                {actionIcon}
                {actionLabel}
              </span>
              <span className="text-slate-600">&middot;</span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatDateFull(entry.created_at)}
              </span>
            </div>
          </div>
        </div>

        {/* Fields */}
        <div className="divide-y divide-slate-700/30">
          <Row label="Entity Type" value={entry.entity_type || "—"} />
          <Row label="Entity ID" value={entry.entity_id || "—"} />
          <Row label="Action" value={actionLabel} />
          <Row label="Timestamp" value={formatDateFull(entry.created_at)} />
        </div>

        {/* Metadata */}
        {Object.keys(meta).length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">
              Metadata
            </h3>
            <div className="space-y-2">
              {Object.entries(meta).map(([key, value]) => (
                <div key={key} className="p-2.5 rounded-lg bg-slate-800/30 border border-slate-700/20">
                  <p className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-1">
                    {key.replace(/_/g, " ")}
                  </p>
                  <p className="text-xs text-slate-300 break-words whitespace-pre-wrap max-h-24 overflow-y-auto">
                    {renderMetadataValue(value)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ActivityPage() {
  const [entries, setEntries] = useState<ActivityEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Filters
  const [search, setSearch] = useState("");
  const [entityTypeFilter, setEntityTypeFilter] = useState("");
  const [actionFilter, setActionFilter] = useState("");

  // Pagination
  const [page, setPage] = useState(0);
  const perPage = 30;

  // Dropdown options
  const [entityTypes, setEntityTypes] = useState<string[]>([]);
  const [actions, setActions] = useState<string[]>([]);

  // Modal state
  const [viewTarget, setViewTarget] = useState<ActivityEntry | null>(null);

  // -----------------------------------------------------------------------
  // Load filter options
  // -----------------------------------------------------------------------

  useEffect(() => {
    Promise.all([
      apiFetch<string[]>("/api/activity/entity-types").catch(() => []),
      apiFetch<string[]>("/api/activity/actions").catch(() => []),
    ]).then(([types, acts]) => {
      setEntityTypes(types);
      setActions(acts);
    });
  }, []);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadActivity = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    try {
      const params = new URLSearchParams({
        page: String(page),
        per_page: String(perPage),
      });
      if (entityTypeFilter) params.set("entity_type", entityTypeFilter);
      if (actionFilter) params.set("action", actionFilter);
      if (search) params.set("search", search);

      const data = await apiFetch<ActivityResponse>(`/api/activity?${params}`);
      setEntries(data.items);
      setTotal(data.total);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [page, entityTypeFilter, actionFilter, search, perPage]);

  useEffect(() => {
    loadActivity();
  }, [loadActivity]);

  // -----------------------------------------------------------------------
  // Pagination
  // -----------------------------------------------------------------------

  const totalPages = Math.max(1, Math.ceil(total / perPage));

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [search, entityTypeFilter, actionFilter]);

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Activity</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {total} event{total !== 1 ? "s" : ""}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => loadActivity(true)}
              disabled={refreshing}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 active:bg-slate-600 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${refreshing ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
        {/* Error banner */}
        {error && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-auto hover:text-red-300 transition-colors">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Search & filters */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[240px] max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              placeholder="Search across events..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors min-w-[130px]"
              value={entityTypeFilter}
              onChange={(e) => setEntityTypeFilter(e.target.value)}
            >
              <option value="">All Types</option>
              {entityTypes.map((t) => {
                const cfg = entityConfig[t];
                return (
                  <option key={t} value={t}>
                    {cfg ? cfg.label : t}
                  </option>
                );
              })}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors min-w-[150px]"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
            >
              <option value="">All Actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {getActionLabel(a)}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          {(search || entityTypeFilter || actionFilter) && (
            <button
              onClick={() => {
                setSearch("");
                setEntityTypeFilter("");
                setActionFilter("");
              }}
              className="text-sm text-slate-400 hover:text-white transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Activity feed */}
        <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 overflow-hidden">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="animate-spin h-8 w-8 text-blue-500" />
            </div>
          ) : entries.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center mb-4">
                <Activity className="w-6 h-6 text-slate-500" />
              </div>
              <p className="text-slate-400 text-sm font-medium">
                {search || entityTypeFilter || actionFilter
                  ? "No events match your filters"
                  : "No activity yet"}
              </p>
              <p className="text-slate-600 text-xs mt-1">
                {search || entityTypeFilter || actionFilter
                  ? "Try adjusting your search or filter criteria"
                  : "Events will appear here as you use the platform"}
              </p>
            </div>
          ) : (
            <>
              {/* Timeline-style feed */}
              <div className="divide-y divide-slate-800/30">
                {entries.map((entry) => {
                  const cf = entityConfig[entry.entity_type || ""];
                  const actionIcon = getActionIcon(entry.action);
                  const actionLabel = getActionLabel(entry.action);
                  const meta = entry.metadata || {};

                  // Build a summary line from metadata
                  let summary = "";
                  if (entry.action === "email_sent") {
                    summary = `To: ${meta.to_email || "?"} — ${truncate(meta.subject, 50)}`;
                  } else if (entry.action === "email_failed") {
                    summary = `To: ${meta.to_email || "?"} — ${truncate(meta.error, 50)}`;
                  } else if (entry.action === "reply_received") {
                    summary = `From: ${meta.from_email || "?"} — Intent: ${meta.reply_intent || "?"}`;
                  } else if (entry.action === "closed") {
                    summary = `Closed at ${formatCurrency(meta.closed_price)} — Net: ${formatCurrency(meta.net_spread)}`;
                  } else if (entry.action === "status_change") {
                    summary = `${meta.from_status || "?"} → ${meta.to_status || "?"}`;
                  } else if (entry.action === "profile_updated") {
                    const changes = meta.changes;
                    if (changes && typeof changes === "object") {
                      const keys = Object.keys(changes);
                      summary = `Updated: ${keys.join(", ")}`;
                    }
                  }

                  return (
                    <div
                      key={entry.id}
                      className="group flex items-start gap-4 px-5 py-4 hover:bg-slate-800/30 transition-colors cursor-pointer"
                      onClick={() => setViewTarget(entry)}
                    >
                      {/* Entity type indicator */}
                      <div className="shrink-0 mt-0.5">
                        <div
                          className={`w-9 h-9 rounded-lg flex items-center justify-center text-xs border ${
                            cf ? cf.color : "bg-slate-700/50 text-slate-400 border-slate-600/30"
                          }`}
                        >
                          {cf ? cf.icon : <Activity className="w-4 h-4" />}
                        </div>
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-medium text-white">
                            {cf ? cf.label : entry.entity_type || "System"}
                          </span>
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-700/50 text-slate-300">
                            {actionIcon}
                            {actionLabel}
                          </span>
                          <span className="text-xs text-slate-500 ml-auto shrink-0">
                            {formatDate(entry.created_at)}
                          </span>
                        </div>
                        {summary && (
                          <p className="text-xs text-slate-400 mt-1.5 leading-relaxed line-clamp-2">
                            {summary}
                          </p>
                        )}
                        {!summary && Object.keys(meta).length > 0 && (
                          <p className="text-xs text-slate-500 mt-1.5 line-clamp-1">
                            {Object.entries(meta)
                              .slice(0, 3)
                              .map(([k, v]) => `${k}: ${String(v).slice(0, 30)}`)
                              .join(" · ")}
                          </p>
                        )}
                      </div>

                      {/* View button */}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setViewTarget(entry);
                        }}
                        className="p-1.5 rounded-md text-slate-500 hover:text-white hover:bg-slate-700 transition-colors opacity-0 group-hover:opacity-100 shrink-0"
                        title="View details"
                      >
                        <Eye className="w-4 h-4" />
                      </button>
                    </div>
                  );
                })}
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between px-5 py-3 border-t border-slate-800/50">
                <p className="text-xs text-slate-500">
                  Showing {page * perPage + 1}–{Math.min((page + 1) * perPage, total)} of {total}
                </p>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={page === 0}
                    className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <span className="text-xs text-slate-500 tabular-nums">
                    {page + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={page >= totalPages - 1}
                    className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                  >
                    <ChevronRight className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </main>

      {/* Activity Detail Modal */}
      <ActivityDetailModal
        open={!!viewTarget}
        onClose={() => setViewTarget(null)}
        entry={viewTarget}
      />
    </div>
  );
}

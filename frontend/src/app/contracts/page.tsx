"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  FileText,
  CheckCircle2,
  X,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  Send,
  Clock,
  DollarSign,
  Users,
  MapPin,
  Building2,
  Loader2,
  Mail,
  ExternalLink,
  ClipboardList,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ContractAlert {
  alert_id: string;
  created_at: string;
  buyer_name: string | null;
  buyer_email: string | null;
  deal_address: string | null;
  deal_state: string | null;
  negotiated_price: number | null;
  my_payout: number | null;
  jv_partner_name: string | null;
  resolved: boolean;
  resolved_at: string | null;
  full_metadata: Record<string, any> | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatCurrency(val: number | null | undefined): string {
  if (val == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(val);
}

function timeSince(iso: string | null): string {
  if (!iso) return "";
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diff = now - then;
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  return `${days}d ago`;
}

function formatDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Next Steps Checklist Item
// ---------------------------------------------------------------------------

function ChecklistItem({
  text,
  checked,
  onChange,
}: {
  text: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 py-1.5 cursor-pointer group">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 w-4 h-4 rounded border-slate-600 bg-slate-800 text-blue-500 focus:ring-blue-500/50 focus:ring-offset-0 cursor-pointer"
      />
      <span className={`text-sm transition-colors ${checked ? "text-slate-500 line-through" : "text-slate-300"}`}>
        {text}
      </span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Expanded Alert Card
// ---------------------------------------------------------------------------

function ExpandedAlertCard({
  alert,
  checklists,
  onChecklistChange,
  onMarkSent,
  marking,
  resolveNotes,
  setResolveNotes,
}: {
  alert: ContractAlert;
  checklists: Record<string, boolean[]>;
  onChecklistChange: (alertId: string, index: number, value: boolean) => void;
  onMarkSent: () => void;
  marking: boolean;
  resolveNotes: string;
  setResolveNotes: (v: string) => void;
}) {
  const meta = alert.full_metadata || {};
  const buyer = meta.buyer || {};
  const deal = meta.deal || {};
  const steps: string[] = meta.suggested_next_steps || [];
  const checklist = checklists[alert.alert_id] || steps.map(() => false);

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start gap-3 py-2 border-b border-slate-700/30 last:border-0">
      <span className="w-32 shrink-0 text-xs text-slate-500">{label}</span>
      <span className="text-sm text-white break-words">{value}</span>
    </div>
  );

  return (
    <div className="space-y-4">
      {/* Buyer Info */}
      <div>
        <h4 className="text-xs font-semibold text-blue-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <Users className="w-3.5 h-3.5" />
          Buyer
        </h4>
        <div className="bg-slate-800/50 rounded-lg p-3 divide-y divide-slate-700/30">
          <Row label="Name" value={buyer.name || alert.buyer_name || "—"} />
          <Row label="Email" value={buyer.email || alert.buyer_email || "—"} />
          <Row label="Closes In" value={buyer.closes_in || "N/A"} />
          <Row label="Title Co." value={buyer.title_company || "N/A"} />
          <Row label="Timeline" value={buyer.closing_timeline || "N/A"} />
        </div>
      </div>

      {/* Deal Financials */}
      <div>
        <h4 className="text-xs font-semibold text-emerald-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <DollarSign className="w-3.5 h-3.5" />
          Deal Financials
        </h4>
        <div className="bg-slate-800/50 rounded-lg p-3 divide-y divide-slate-700/30">
          <Row label="Address" value={deal.address || alert.deal_address || "—"} />
          <Row label="City / State" value={`${deal.city || ""}, ${deal.state || alert.deal_state || ""}`} />
          <Row label="Asking Price" value={formatCurrency(deal.asking_price)} />
          <Row label="Floor Price" value={formatCurrency(deal.floor_price)} />
          <Row label="Contract Price" value={formatCurrency(deal.contract_price)} />
          <Row label="Assignment Fee" value={formatCurrency(deal.assignment_fee)} />
          <Row label="My Payout" value={<span className="font-semibold text-emerald-400">{formatCurrency(deal.my_payout || alert.my_payout)}</span>} />
          <Row label="Negotiated Price" value={formatCurrency(meta.negotiated_price || alert.negotiated_price)} />
          <Row label="JV Partner" value={deal.jv_partner || alert.jv_partner_name || "—"} />
        </div>
      </div>

      {/* Thread Summary */}
      {meta.thread_summary && (
        <div>
          <h4 className="text-xs font-semibold text-purple-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <ClipboardList className="w-3.5 h-3.5" />
            Thread Summary
          </h4>
          <div className="bg-slate-800/50 rounded-lg p-3 text-sm text-slate-300 leading-relaxed">
            {meta.thread_summary}
          </div>
        </div>
      )}

      {/* Suggested Next Steps */}
      {steps.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-amber-400 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <ClipboardList className="w-3.5 h-3.5" />
            Next Steps
          </h4>
          <div className="bg-slate-800/50 rounded-lg p-3">
            {steps.map((step: string, i: number) => (
              <ChecklistItem
                key={i}
                text={step}
                checked={checklist[i] || false}
                onChange={(v) => onChecklistChange(alert.alert_id, i, v)}
              />
            ))}
          </div>
        </div>
      )}

      {/* Mark Sent */}
      {!alert.resolved && (
        <div className="border-t border-slate-700/30 pt-4 space-y-3">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Notes (e.g. "Sent via DocuSign at 3pm")
            </label>
            <input
              type="text"
              value={resolveNotes}
              onChange={(e) => setResolveNotes(e.target.value)}
              placeholder="Optional notes…"
              className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
            />
          </div>
          <button
            onClick={onMarkSent}
            disabled={marking}
            className="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-600 transition-colors shadow-lg shadow-emerald-600/20 disabled:opacity-50"
          >
            {marking ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <CheckCircle2 className="w-4 h-4" />
            )}
            Mark as Sent
          </button>
        </div>
      )}

      {/* Resolved info */}
      {alert.resolved && (
        <div className="flex items-center gap-2 text-xs text-emerald-400 bg-emerald-500/10 px-3 py-2 rounded-lg border border-emerald-500/20">
          <CheckCircle2 className="w-4 h-4" />
          Resolved {formatDate(alert.resolved_at)}
          {meta.resolution_notes && <span className="text-slate-400">— {meta.resolution_notes}</span>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function ContractsPage() {
  const [alerts, setAlerts] = useState<ContractAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Expanded alert IDs
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // Checklist state: alert_id → boolean[]
  const [checklists, setChecklists] = useState<Record<string, boolean[]>>({});

  // Resolve state
  const [markingId, setMarkingId] = useState<string | null>(null);
  const [resolveNotes, setResolveNotes] = useState("");

  // Toast
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ContractAlert[]>("/api/alerts/contract-ready");
      setAlerts(data);
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

  // Auto-dismiss toast
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  // -----------------------------------------------------------------------
  // Handlers
  // -----------------------------------------------------------------------

  const toggleExpand = (alertId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(alertId)) {
        next.delete(alertId);
      } else {
        next.add(alertId);
      }
      return next;
    });
  };

  const handleChecklistChange = (alertId: string, index: number, value: boolean) => {
    setChecklists((prev) => {
      const list = [...(prev[alertId] || [])];
      list[index] = value;
      return { ...prev, [alertId]: list };
    });
  };

  const handleMarkSent = async (alert: ContractAlert) => {
    setMarkingId(alert.alert_id);
    setError(null);
    try {
      await apiFetch(`/api/alerts/contract-ready/${alert.alert_id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ notes: resolveNotes || undefined }),
      });
      setToast({ message: "Contract marked as sent.", type: "success" });
      setResolveNotes("");
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setMarkingId(null);
    }
  };

  // -----------------------------------------------------------------------
  // Stats
  // -----------------------------------------------------------------------

  const unresolved = alerts.filter((a) => !a.resolved);
  const totalPayout = unresolved.reduce((sum, a) => sum + (a.my_payout || 0), 0);

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Contracts</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {unresolved.length} pending contract{alerts.length > 0 && ` · ${alerts.length} total`}
            </p>
          </div>
          <button
            onClick={loadData}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors"
          >
            <Loader2 className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-6 space-y-6">
        {/* Toast */}
        {toast && (
          <div
            className={`flex items-center gap-3 px-4 py-3 rounded-lg text-sm ${
              toast.type === "success"
                ? "bg-emerald-500/10 border border-emerald-500/20 text-emerald-400"
                : "bg-red-500/10 border border-red-500/20 text-red-400"
            }`}
          >
            {toast.type === "success" ? (
              <CheckCircle2 className="w-5 h-5 shrink-0" />
            ) : (
              <AlertCircle className="w-5 h-5 shrink-0" />
            )}
            <span>{toast.message}</span>
            <button onClick={() => setToast(null)} className="ml-auto hover:opacity-70 transition-opacity">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

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

        {/* Summary stats */}
        {unresolved.length > 0 && (
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center transition-all duration-300 hover:border-slate-700/50">
              <FileText className="w-5 h-5 text-amber-400 mx-auto mb-2" />
              <p className="text-2xl font-bold text-white">{unresolved.length}</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Pending Contracts</p>
            </div>
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center transition-all duration-300 hover:border-slate-700/50">
              <DollarSign className="w-5 h-5 text-emerald-400 mx-auto mb-2" />
              <p className="text-2xl font-bold text-emerald-400">{formatCurrency(totalPayout)}</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Total Payout Pending</p>
            </div>
          </div>
        )}

        {/* Alert cards */}
        {loading && alerts.length === 0 ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 className="animate-spin h-8 w-8 text-blue-500" />
          </div>
        ) : alerts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-center">
            <div className="w-14 h-14 rounded-full bg-slate-800 flex items-center justify-center mb-4">
              <CheckCircle2 className="w-7 h-7 text-slate-500" />
            </div>
            <p className="text-slate-400 text-sm font-medium">No contract alerts</p>
            <p className="text-slate-600 text-xs mt-1">
              When a buyer responds with interest, a contract alert will appear here.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {alerts.map((alert) => {
              const isExpanded = expandedIds.has(alert.alert_id);
              const isMarking = markingId === alert.alert_id;
              const meta = alert.full_metadata || {};

              return (
                <div
                  key={alert.alert_id}
                  className={`rounded-xl border transition-all duration-300 ${
                    alert.resolved
                      ? "border-slate-800/30 bg-slate-900/30 opacity-60"
                      : "border-slate-700/50 bg-slate-900/80 hover:border-slate-600/50 shadow-lg shadow-slate-900/50"
                  }`}
                >
                  {/* Card Header */}
                  <button
                    onClick={() => toggleExpand(alert.alert_id)}
                    className="w-full flex items-start gap-4 px-5 py-4 text-left"
                  >
                    {/* Icon */}
                    <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${
                      alert.resolved
                        ? "bg-emerald-500/10 text-emerald-400"
                        : "bg-amber-500/10 text-amber-400 animate-pulse-slow"
                    }`}>
                      {alert.resolved ? (
                        <CheckCircle2 className="w-5 h-5" />
                      ) : (
                        <FileText className="w-5 h-5" />
                      )}
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-semibold text-white truncate">
                          {alert.buyer_name || "Unknown Buyer"}
                        </p>
                        {!alert.resolved && (
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-red-500/10 text-red-400 border border-red-500/20">
                            Action Needed
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 mt-1 text-xs text-slate-500">
                        <MapPin className="w-3 h-3" />
                        <span>{alert.deal_address || "Unknown deal"}{alert.deal_state ? `, ${alert.deal_state}` : ""}</span>
                      </div>
                      <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-500">
                        <span className="flex items-center gap-1">
                          <DollarSign className="w-3 h-3" />
                          {formatCurrency(alert.my_payout)}
                        </span>
                        <span className="flex items-center gap-1">
                          <Users className="w-3 h-3" />
                          {alert.jv_partner_name || "—"}
                        </span>
                        <span className="flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {timeSince(alert.created_at)}
                        </span>
                      </div>
                    </div>

                    {/* Expand toggle */}
                    <div className="shrink-0 pt-1">
                      {isExpanded ? (
                        <ChevronUp className="w-4 h-4 text-slate-500" />
                      ) : (
                        <ChevronDown className="w-4 h-4 text-slate-500" />
                      )}
                    </div>
                  </button>

                  {/* Expanded Details */}
                  {isExpanded && (
                    <div className="px-5 pb-5 border-t border-slate-700/30 pt-4">
                      <ExpandedAlertCard
                        alert={alert}
                        checklists={checklists}
                        onChecklistChange={handleChecklistChange}
                        onMarkSent={() => handleMarkSent(alert)}
                        marking={isMarking}
                        resolveNotes={resolveNotes}
                        setResolveNotes={setResolveNotes}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        {/* Bottom spacing */}
        <div className="h-6" />
      </main>
    </div>
  );
}

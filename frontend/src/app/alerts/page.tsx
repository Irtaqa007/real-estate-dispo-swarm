"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  AlertTriangle,
  CheckCircle2,
  X,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  DollarSign,
  Users,
  MapPin,
  ThumbsDown,
  ThumbsUp,
  Send,
  Loader2,
  TrendingDown,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface NegotiationAlert {
  alert_id: string;
  created_at: string;
  buyer_name: string;
  buyer_email: string;
  deal_address: string;
  counter_price: number;
  floor_price: number;
  gap: number;
  deal_id: string;
  campaign_id: string;
  buyer_id: string;
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

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function AlertsPage() {
  const [alerts, setAlerts] = useState<NegotiationAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Expanded alert IDs
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // Action-specific state
  const [actingOnId, setActingOnId] = useState<string | null>(null);
  const [finalPrice, setFinalPrice] = useState<Record<string, string>>({});
  const [counterPrice, setCounterPrice] = useState<Record<string, string>>({});
  const [showCounterInput, setShowCounterInput] = useState<Record<string, boolean>>({});

  // Toast
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<NegotiationAlert[]>("/api/alerts/negotiation");
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
      if (next.has(alertId)) next.delete(alertId);
      else next.add(alertId);
      return next;
    });
  };

  const handleApprove = async (alert: NegotiationAlert) => {
    const fp = parseFloat(finalPrice[alert.alert_id]);
    if (isNaN(fp) || fp <= 0) return;
    setActingOnId(alert.alert_id);
    setError(null);
    try {
      await apiFetch(`/api/alerts/negotiation/${alert.alert_id}/approve`, {
        method: "POST",
        body: JSON.stringify({ final_price: fp }),
      });
      setToast({
        message: `Approved at ${formatCurrency(fp)}. Buyer notified.`,
        type: "success",
      });
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setActingOnId(null);
    }
  };

  const handleReject = async (alert: NegotiationAlert) => {
    setActingOnId(alert.alert_id);
    setError(null);
    try {
      await apiFetch(`/api/alerts/negotiation/${alert.alert_id}/reject`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setToast({
        message: "Counter declined. Buyer notified.",
        type: "success",
      });
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setActingOnId(null);
    }
  };

  const handleCounter = async (alert: NegotiationAlert) => {
    const co = parseFloat(counterPrice[alert.alert_id]);
    if (isNaN(co) || co <= 0) return;
    setActingOnId(alert.alert_id);
    setError(null);
    try {
      await apiFetch(`/api/alerts/negotiation/${alert.alert_id}/reject`, {
        method: "POST",
        body: JSON.stringify({ counter_offer: co }),
      });
      setToast({
        message: `Countered at ${formatCurrency(co)}. Buyer notified.`,
        type: "success",
      });
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setActingOnId(null);
    }
  };

  // -----------------------------------------------------------------------
  // Stats
  // -----------------------------------------------------------------------

  const totalGap = alerts.reduce((sum, a) => sum + (a.gap || 0), 0);

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Alerts</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {alerts.length} pending negotiation{alerts.length !== 1 ? "s" : ""}
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
        {alerts.length > 0 && (
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center">
              <AlertTriangle className="w-5 h-5 text-amber-400 mx-auto mb-2" />
              <p className="text-2xl font-bold text-white">{alerts.length}</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Pending Negotiations</p>
            </div>
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center">
              <TrendingDown className="w-5 h-5 text-red-400 mx-auto mb-2" />
              <p className="text-2xl font-bold text-red-400">{formatCurrency(totalGap)}</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Total Gap Below Floor</p>
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
            <p className="text-slate-400 text-sm font-medium">No negotiation alerts</p>
            <p className="text-slate-600 text-xs mt-1">
              When a buyer counters below the floor price, an alert will appear here for your review.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {alerts.map((alert) => {
              const isExpanded = expandedIds.has(alert.alert_id);
              const isActing = actingOnId === alert.alert_id;
              const fpVal = finalPrice[alert.alert_id] || "";
              const coVal = counterPrice[alert.alert_id] || "";
              const showCounter = showCounterInput[alert.alert_id] || false;

              return (
                <div
                  key={alert.alert_id}
                  className={`rounded-xl border transition-all duration-300 ${
                    alert.resolved
                      ? "border-slate-800/30 bg-slate-900/30 opacity-60"
                      : "border-amber-500/30 bg-slate-900/80 hover:border-amber-500/50 shadow-lg shadow-amber-900/20"
                  }`}
                >
                  {/* Card Header */}
                  <button
                    onClick={() => toggleExpand(alert.alert_id)}
                    className="w-full flex items-start gap-4 px-5 py-4 text-left"
                  >
                    {/* Icon */}
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 bg-amber-500/10 text-amber-400">
                      <AlertTriangle className="w-5 h-5" />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="text-sm font-semibold text-white truncate">
                          {alert.buyer_name || "Unknown Buyer"}
                        </p>
                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider bg-red-500/10 text-red-400 border border-red-500/20">
                          Below Floor
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 text-xs text-slate-500">
                        <MapPin className="w-3 h-3" />
                        <span>{alert.deal_address || "Unknown deal"}</span>
                      </div>
                      <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-500">
                        <span className="flex items-center gap-1">
                          <Users className="w-3 h-3" />
                          {alert.buyer_email}
                        </span>
                        <span className="flex items-center gap-1">
                          <DollarSign className="w-3 h-3 text-red-400" />
                          <span className="text-red-400 font-medium">
                            {formatCurrency(alert.gap)} gap
                          </span>
                        </span>
                        <span>{timeSince(alert.created_at)}</span>
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

                  {/* Expanded Actions */}
                  {isExpanded && !alert.resolved && (
                    <div className="px-5 pb-5 border-t border-slate-700/30 pt-4 space-y-4">
                      {/* Price Summary */}
                      <div className="grid grid-cols-3 gap-3 bg-slate-800/50 rounded-lg p-3 text-sm">
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Counter Offer</p>
                          <p className="text-lg font-bold text-white">{formatCurrency(alert.counter_price)}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Floor Price</p>
                          <p className="text-lg font-bold text-amber-400">{formatCurrency(alert.floor_price)}</p>
                        </div>
                        <div className="text-center">
                          <p className="text-xs text-slate-500 mb-0.5">Gap</p>
                          <p className="text-lg font-bold text-red-400">-{formatCurrency(alert.gap)}</p>
                        </div>
                      </div>

                      {/* Approve */}
                      <div className="bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-3">
                        <p className="text-xs font-medium text-emerald-400 mb-2 flex items-center gap-1.5">
                          <ThumbsUp className="w-3.5 h-3.5" />
                          Approve at your price
                        </p>
                        <div className="flex items-center gap-2">
                          <div className="relative flex-1">
                            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">$</span>
                            <input
                              type="number"
                              placeholder="Final price"
                              value={fpVal}
                              onChange={(e) => setFinalPrice(prev => ({ ...prev, [alert.alert_id]: e.target.value }))}
                              className="w-full pl-7 pr-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-colors"
                            />
                          </div>
                          <button
                            onClick={() => handleApprove(alert)}
                            disabled={isActing || !fpVal || isNaN(parseFloat(fpVal)) || parseFloat(fpVal) <= 0}
                            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-white bg-emerald-600 hover:bg-emerald-500 transition-colors disabled:opacity-50"
                          >
                            {isActing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
                            Accept
                          </button>
                        </div>
                      </div>

                      {/* Counter or Decline */}
                      <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-3">
                        <p className="text-xs font-medium text-red-400 mb-2 flex items-center gap-1.5">
                          <ThumbsDown className="w-3.5 h-3.5" />
                          Counter or Decline
                        </p>
                        <div className="space-y-2">
                          {showCounter ? (
                            <div className="flex items-center gap-2">
                              <div className="relative flex-1">
                                <span className="absolute left-3 top-1/2 -translate-y-1/2 text-xs text-slate-500">$</span>
                                <input
                                  type="number"
                                  placeholder="Your counter price"
                                  value={coVal}
                                  onChange={(e) => setCounterPrice(prev => ({ ...prev, [alert.alert_id]: e.target.value }))}
                                  className="w-full pl-7 pr-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:border-amber-500/50 transition-colors"
                                />
                              </div>
                              <button
                                onClick={() => handleCounter(alert)}
                                disabled={isActing || !coVal || isNaN(parseFloat(coVal)) || parseFloat(coVal) <= 0}
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-white bg-amber-600 hover:bg-amber-500 transition-colors disabled:opacity-50"
                              >
                                {isActing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                                Send
                              </button>
                              <button
                                onClick={() => setShowCounterInput(prev => ({ ...prev, [alert.alert_id]: false }))}
                                className="px-3 py-2 text-xs text-slate-400 hover:text-white transition-colors"
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <button
                                onClick={() => setShowCounterInput(prev => ({ ...prev, [alert.alert_id]: true }))}
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 transition-colors"
                              >
                                <Send className="w-3.5 h-3.5" />
                                Counter Offer
                              </button>
                              <button
                                onClick={() => handleReject(alert)}
                                disabled={isActing}
                                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium text-red-300 bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 transition-colors disabled:opacity-50"
                              >
                                {isActing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <X className="w-3.5 h-3.5" />}
                                Decline
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Resolved state */}
                  {alert.resolved && (
                    <div className="px-5 pb-4 border-t border-slate-700/30 pt-3">
                      <div className="flex items-center gap-2 text-xs text-slate-500">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                        Resolved
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="h-6" />
      </main>
    </div>
  );
}

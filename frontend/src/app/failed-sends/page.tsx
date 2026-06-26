"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  Search,
  RefreshCw,
  X,
  AlertCircle,
  CheckCircle2,
  Loader2,
  Mail,
  AlertTriangle,
  RotateCcw,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FailedCampaign {
  id: string;
  campaign_id: string;
  error_message: string;
  retry_count: number;
  last_retry_at: string | null;
  resolved: boolean;
  created_at: string;
  campaign_subject: string | null;
  buyer_email: string | null;
  buyer_name: string | null;
}

interface RetryResult {
  id: string;
  campaign_id: string;
  retry_count: number;
  success: boolean;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function truncate(text: string | null, max: number) {
  if (!text) return "—";
  if (text.length <= max) return text;
  return text.slice(0, max) + "\u2026";
}

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function FailedSendsPage() {
  const [entries, setEntries] = useState<FailedCampaign[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Retry state — track which entries are currently being retried
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [retryErrors, setRetryErrors] = useState<Record<string, string>>({});
  const [retrySuccesses, setRetrySuccesses] = useState<Record<string, boolean>>({});

  // Search state
  const [search, setSearch] = useState("");

  // Pagination
  const [page, setPage] = useState(0);
  const perPage = 15;

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<FailedCampaign[]>("/api/failed-campaigns");
      setEntries(data);
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

  // -----------------------------------------------------------------------
  // Filtering
  // -----------------------------------------------------------------------

  const filtered = entries.filter((e) => {
    const q = search.toLowerCase();
    if (!q) return true;
    return (
      (e.buyer_email || "").toLowerCase().includes(q) ||
      (e.buyer_name || "").toLowerCase().includes(q) ||
      (e.campaign_subject || "").toLowerCase().includes(q) ||
      e.error_message.toLowerCase().includes(q)
    );
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * perPage, safePage * perPage + perPage);

  // Reset page when search changes
  useEffect(() => {
    setPage(0);
  }, [search]);

  // -----------------------------------------------------------------------
  // Retry action
  // -----------------------------------------------------------------------

  async function handleRetry(entry: FailedCampaign) {
    setRetryingId(entry.id);
    setRetryErrors((prev) => {
      const copy = { ...prev };
      delete copy[entry.id];
      return copy;
    });
    setRetrySuccesses((prev) => {
      const copy = { ...prev };
      delete copy[entry.id];
      return copy;
    });

    try {
      const result = await apiFetch<RetryResult>(
        `/api/failed-campaigns/${entry.id}/retry`,
        { method: "POST" }
      );
      if (result.success) {
        setRetrySuccesses((prev) => ({ ...prev, [entry.id]: true }));
        // Remove the entry from the list after a short delay
        setTimeout(async () => {
          await loadData();
        }, 1500);
      } else {
        setRetryErrors((prev) => ({
          ...prev,
          [entry.id]: result.error || "Retry failed",
        }));
      }
    } catch (err: any) {
      setRetryErrors((prev) => ({
        ...prev,
        [entry.id]: err.message || "Retry request failed",
      }));
    } finally {
      setRetryingId(null);
    }
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  const unresolvedCount = entries.filter((e) => !e.resolved).length;

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Failed Sends</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {unresolvedCount} unresolved failure{unresolvedCount !== 1 ? "s" : ""}
              {filtered.length !== entries.length &&
                ` (filtered from ${entries.length})`}
            </p>
          </div>
          <div className="flex items-center gap-3">
            {unresolvedCount > 0 && (
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-red-500/10 border border-red-500/20">
                <AlertTriangle className="w-4 h-4 text-red-400" />
                <span className="text-sm font-medium text-red-400">
                  {unresolvedCount} need{unresolvedCount !== 1 ? "" : "s"} attention
                </span>
              </div>
            )}
            <button
              onClick={loadData}
              disabled={loading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
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
            <button
              onClick={() => setError(null)}
              className="ml-auto hover:text-red-300 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* Search */}
        <div className="relative max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
          <input
            className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
            placeholder="Search by email, subject, or error…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* Table card */}
        <div className="card-glass overflow-hidden transition-all duration-300 hover:border-slate-700/50">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="animate-spin h-8 w-8 text-blue-500" />
            </div>
          ) : paged.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-14 h-14 rounded-full bg-emerald-500/10 flex items-center justify-center mb-4">
                <CheckCircle2 className="w-7 h-7 text-emerald-400" />
              </div>
              <p className="text-slate-400 text-sm font-medium">
                {search
                  ? "No failed sends match your search"
                  : "No failed sends"}
              </p>
              <p className="text-slate-600 text-xs mt-1">
                {search
                  ? "Try adjusting your search criteria"
                  : "All emails have been sent successfully"}
              </p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Recipient
                      </th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Subject
                      </th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Error Reason
                      </th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Failed At
                      </th>
                      <th className="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Retries
                      </th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {paged.map((entry, i) => (
                      <tr
                        key={entry.id}
                        className="group hover:bg-slate-800/30 transition-colors"
                        style={{
                          animation: `fadeIn 0.3s ease-out ${i * 25}ms forwards`,
                          opacity: 0,
                        }}
                      >
                        <td className="px-4 py-3 max-w-[200px]">
                          <div className="truncate">
                            <p className="text-white text-sm truncate">
                              {entry.buyer_name || truncate(entry.buyer_email, 30) || "—"}
                            </p>
                            {entry.buyer_name && entry.buyer_email && (
                              <p className="text-[10px] text-slate-500 truncate">
                                {entry.buyer_email}
                              </p>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3 max-w-[220px] text-slate-300">
                          <span title={entry.campaign_subject || ""}>
                            {truncate(entry.campaign_subject, 40)}
                          </span>
                        </td>
                        <td className="px-4 py-3 max-w-[250px]">
                          <div className="flex items-start gap-1.5">
                            <AlertCircle className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />
                            <span
                              className="text-xs text-red-300 line-clamp-2"
                              title={entry.error_message}
                            >
                              {entry.error_message}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-slate-500 text-xs whitespace-nowrap">
                          {formatDate(entry.created_at)}
                        </td>
                        <td className="px-4 py-3 text-center">
                          <span className="inline-flex items-center justify-center min-w-[24px] px-1.5 py-0.5 rounded-full text-xs font-medium bg-slate-700/50 text-slate-400 tabular-nums">
                            {entry.retry_count}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center justify-end gap-2">
                            {retrySuccesses[entry.id] ? (
                              <span className="inline-flex items-center gap-1 text-xs text-emerald-400">
                                <CheckCircle2 className="w-3.5 h-3.5" />
                                Sent!
                              </span>
                            ) : (
                              <button
                                onClick={() => handleRetry(entry)}
                                disabled={retryingId === entry.id}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-blue-600 hover:bg-blue-500 active:bg-blue-600 transition-colors disabled:opacity-50 shadow-lg shadow-blue-600/20"
                              >
                                {retryingId === entry.id ? (
                                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                ) : (
                                  <RotateCcw className="w-3.5 h-3.5" />
                                )}
                                Retry
                              </button>
                            )}
                          </div>
                          {retryErrors[entry.id] && (
                            <p className="text-[10px] text-red-400 mt-1 max-w-[200px] text-right">
                              {retryErrors[entry.id]}
                            </p>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between px-4 py-3 border-t border-slate-800/50">
                <p className="text-xs text-slate-500">
                  Showing {safePage * perPage + 1}–
                  {Math.min((safePage + 1) * perPage, filtered.length)} of{" "}
                  {filtered.length}
                </p>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    disabled={safePage === 0}
                    className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 disabled:opacity-30 disabled:pointer-events-none transition-colors"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <span className="text-xs text-slate-500 tabular-nums">
                    {safePage + 1} / {totalPages}
                  </span>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    disabled={safePage >= totalPages - 1}
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
    </div>
  );
}

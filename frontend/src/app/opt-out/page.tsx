"use client";

import { useEffect, useState, useCallback } from "react";
import { apiFetch } from "@/lib/api";
import { usePolling } from "@/hooks/usePolling";
import {
  Ban,
  UserX,
  Mail,
  Calendar,
  RefreshCw,
  AlertCircle,
  X,
  CheckCircle2,
  ArrowLeft,
} from "lucide-react";
import Link from "next/link";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface OptedOutBuyer {
  id: string;
  full_name: string;
  email: string;
  unsubscribed_at: string | null;
  status: string;
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

// ---------------------------------------------------------------------------
// Confirmation Dialog
// ---------------------------------------------------------------------------

function ConfirmDialog({
  open,
  onClose,
  name,
  onConfirm,
  loading,
}: {
  open: boolean;
  onClose: () => void;
  name: string;
  onConfirm: () => void;
  loading: boolean;
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
      <div className="relative w-full max-w-md rounded-xl border border-slate-700/50 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/50">
          <h2 className="text-base font-semibold text-white">Re-subscribe Buyer</h2>
          <button
            onClick={onClose}
            disabled={loading}
            className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-5 py-4 space-y-4">
          <p className="text-sm text-slate-300">
            Are you sure you want to re-subscribe{" "}
            <span className="font-semibold text-white">{name}</span>?
            This will set their status to Active so they can receive campaign emails again.
          </p>
          <div className="flex justify-end gap-3 pt-2 border-t border-slate-700/50">
            <button
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={onConfirm}
              disabled={loading}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-500 transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {loading ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Re-subscribing...
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-4 h-4" />
                  Re-subscribe
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function OptOutPage() {
  const [buyers, setBuyers] = useState<OptedOutBuyer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Confirm dialog state
  const [confirmTarget, setConfirmTarget] = useState<OptedOutBuyer | null>(null);
  const [resubscribing, setResubscribing] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<OptedOutBuyer[]>("/api/buyers/opted-out");
      setBuyers(data);
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

  async function handleResubscribe() {
    if (!confirmTarget) return;
    setResubscribing(true);
    setError(null);
    setSuccessMsg(null);
    try {
      await apiFetch(`/api/buyers/${confirmTarget.id}/opt-out`, {
        method: "DELETE",
      });
      setSuccessMsg(`${confirmTarget.full_name} has been re-subscribed successfully`);
      // Remove from local list
      setBuyers((prev) => prev.filter((b) => b.id !== confirmTarget.id));
      setConfirmTarget(null);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setResubscribing(false);
    }
  }

  if (loading && buyers.length === 0) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 animate-pulse mx-auto" />
          <p className="text-sm text-slate-500 animate-pulse">Loading opted-out buyers...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/buyers"
              className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
            </Link>
            <div>
              <div className="flex items-center gap-2.5">
                <h1 className="text-2xl font-bold text-white tracking-tight">Opt-Out List</h1>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-800 border border-slate-700/50 text-xs font-medium">
                  <UserX className="w-3 h-3 text-red-400" />
                  <span className="text-slate-300">{buyers.length}</span>
                  <span className="text-slate-500">on DNC list</span>
                </span>
              </div>
              <p className="text-sm text-slate-500 mt-0.5">
                Manage buyers who have unsubscribed or been marked as Do Not Contact
              </p>
            </div>
          </div>
          <button
            onClick={loadData}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 border border-slate-700/50 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-6 space-y-5">
        {/* Success banner */}
        {successMsg && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">
            <CheckCircle2 className="w-5 h-5 shrink-0" />
            <span>{successMsg}</span>
            <button onClick={() => setSuccessMsg(null)} className="ml-auto hover:text-emerald-300 transition-colors">
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

        {/* Summary cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4">
            <div className="flex items-center gap-2 mb-1.5">
              <UserX className="w-4 h-4 text-red-400" />
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total Opted Out</span>
            </div>
            <p className="text-2xl font-bold text-white tabular-nums">{buyers.length}</p>
          </div>
          <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4">
            <div className="flex items-center gap-2 mb-1.5">
              <Ban className="w-4 h-4 text-amber-400" />
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Do Not Contact</span>
            </div>
            <p className="text-2xl font-bold text-white tabular-nums">
              {buyers.filter((b) => b.status === "Do Not Contact").length}
            </p>
          </div>
          <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4">
            <div className="flex items-center gap-2 mb-1.5">
              <Mail className="w-4 h-4 text-blue-400" />
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Has Email</span>
            </div>
            <p className="text-2xl font-bold text-white tabular-nums">
              {buyers.filter((b) => b.email).length}
            </p>
          </div>
        </div>

        {/* Table */}
        <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-800/50">
                  <th className="text-left px-5 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Name</th>
                  <th className="text-left px-5 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Email</th>
                  <th className="text-left px-5 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Status</th>
                  <th className="text-left px-5 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Opted Out</th>
                  <th className="text-right px-5 py-3.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/30">
                {buyers.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="px-5 py-12 text-center">
                      <div className="flex flex-col items-center gap-2">
                        <CheckCircle2 className="w-8 h-8 text-emerald-400/50" />
                        <p className="text-sm text-slate-500 font-medium">No opted-out buyers</p>
                        <p className="text-xs text-slate-600">All buyers are currently active</p>
                      </div>
                    </td>
                  </tr>
                ) : (
                  buyers.map((b, i) => (
                    <tr
                      key={b.id}
                      className="group hover:bg-slate-800/30 transition-colors"
                      style={{ animation: `fadeIn 0.3s ease-out ${i * 30}ms forwards`, opacity: 0 }}
                    >
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-red-500/20 to-orange-500/20 flex items-center justify-center text-xs font-semibold text-red-400">
                            {b.full_name
                              .split(" ")
                              .map((n) => n[0])
                              .join("")
                              .slice(0, 2)
                              .toUpperCase()}
                          </div>
                          <span className="font-medium text-white">{b.full_name}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="text-slate-300">{b.email}</span>
                      </td>
                      <td className="px-5 py-3.5">
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-red-500/10 text-red-400">
                          <Ban className="w-3 h-3" />
                          Do Not Contact
                        </span>
                      </td>
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-1.5 text-slate-400">
                          <Calendar className="w-3.5 h-3.5" />
                          <span className="text-sm">{formatDate(b.unsubscribed_at)}</span>
                        </div>
                      </td>
                      <td className="px-5 py-3.5 text-right">
                        <button
                          onClick={() => setConfirmTarget(b)}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-emerald-400 hover:text-white bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20 hover:border-emerald-500/40 transition-all"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          Re-subscribe
                        </button>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Info note */}
        <div className="rounded-lg bg-slate-900/30 border border-slate-800/30 px-4 py-3">
          <p className="text-xs text-slate-500">
            Re-subscribing a buyer sets their status to Active and clears their opt-out date,
            allowing them to receive campaign emails again.
          </p>
        </div>
      </main>

      {/* Confirm Dialog */}
      <ConfirmDialog
        open={!!confirmTarget}
        onClose={() => setConfirmTarget(null)}
        name={confirmTarget?.full_name || ""}
        onConfirm={handleResubscribe}
        loading={resubscribing}
      />
    </div>
  );
}

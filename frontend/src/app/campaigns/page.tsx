"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  Search,
  Eye,
  Send,
  RefreshCw,
  X,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Mail,
  Clock,
  MessageSquare,
  Ban,
  Loader2,
  Zap,
  Info,
  ExternalLink,
  Reply,
  FileText,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Campaign {
  id: string;
  deal_id: string;
  buyer_id: string;
  touch_number: number;
  status: string;
  sent_at: string | null;
  subject: string | null;
  body: string | null;
  reply_received_at: string | null;
  reply_body: string | null;
  reply_intent: string | null;
  ai_extracted_insights: string | null;
  buyer_profile_updated: boolean;
  created_at: string;
}

interface Deal {
  id: string;
  address: string;
}

interface Buyer {
  id: string;
  full_name: string;
  email: string;
}

interface CheckRepliesResult {
  total_replies_found: number;
  replies_processed: number;
  results: Array<{
    from_email: string;
    subject: string;
    reply_intent: string;
    matched: boolean;
    error?: string;
  }>;
}

interface SendResult {
  campaign_id: string;
  to_email: string;
  subject: string;
  message_id: string;
  status: string;
  sent_at: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const statusConfig: Record<string, { label: string; bg: string; dot: string }> = {
  Queued: { label: "Queued", bg: "bg-slate-500/10 text-slate-400", dot: "bg-slate-400" },
  Sent: { label: "Sent", bg: "bg-blue-500/10 text-blue-400", dot: "bg-blue-400" },
  Replied: { label: "Replied", bg: "bg-emerald-500/10 text-emerald-400", dot: "bg-emerald-400" },
  Ready: { label: "Ready", bg: "bg-amber-500/10 text-amber-400", dot: "bg-amber-400" },
  Failed: { label: "Failed", bg: "bg-red-500/10 text-red-400", dot: "bg-red-400" },
  Bounced: { label: "Bounced", bg: "bg-red-500/10 text-red-400", dot: "bg-red-400" },
  Paused: { label: "Paused", bg: "bg-purple-500/10 text-purple-400", dot: "bg-purple-400" },
};

const CAMPAIGN_STATUSES = ["", "Queued", "Ready", "Sent", "Replied", "Failed", "Bounced", "Paused"];
const TOUCH_NUMBERS = [0, 1, 2, 3, 4, 5, 6];

function truncate(text: string | null, max: number) {
  if (!text) return "—";
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
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

function formatDateShort(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
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
      <div className="relative w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border border-slate-700/50 bg-slate-900 shadow-2xl">
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
// View modal
// ---------------------------------------------------------------------------

function ViewCampaignModal({
  open,
  onClose,
  campaign,
  dealAddress,
  buyerName,
  buyerEmail,
}: {
  open: boolean;
  onClose: () => void;
  campaign: Campaign | null;
  dealAddress: string;
  buyerName: string;
  buyerEmail: string;
}) {
  if (!campaign) return null;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start gap-4 py-2.5 border-b border-slate-700/30 last:border-0">
      <span className="w-28 shrink-0 text-sm text-slate-500">{label}</span>
      <span className="text-sm text-white break-words">{value}</span>
    </div>
  );

  return (
    <Modal open={open} onClose={onClose} title={`Email #${campaign.touch_number}`}>
      <div className="space-y-4">
        {/* Summary */}
        <div className="divide-y divide-slate-700/30">
          <Row label="Deal" value={dealAddress} />
          <Row label="Buyer" value={`${buyerName} (${buyerEmail})`} />
          <Row label="Touch #" value={`${campaign.touch_number} of 6`} />
          <Row
            label="Status"
            value={
              <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${statusConfig[campaign.status]?.bg || ""}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${statusConfig[campaign.status]?.dot || ""}`} />
                {campaign.status}
              </span>
            }
          />
          <Row label="Sent At" value={formatDate(campaign.sent_at)} />
          <Row label="Created" value={formatDate(campaign.created_at)} />
        </div>

        {/* Subject */}
        <div>
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Subject</h3>
          <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-700/30 text-sm text-white">
            {campaign.subject || "—"}
          </div>
        </div>

        {/* Email Body */}
        <div>
          <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Email Body</h3>
          <div className="p-3 rounded-lg bg-slate-800/50 border border-slate-700/30 text-sm text-slate-200 whitespace-pre-wrap max-h-60 overflow-y-auto leading-relaxed">
            {campaign.body || "—"}
          </div>
        </div>

        {/* Reply */}
        {campaign.status === "Replied" && (
          <>
            <div className="border-t border-slate-700/30 pt-4">
              <h3 className="text-xs font-semibold text-emerald-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
                <Reply className="w-3.5 h-3.5" />
                Reply Received
              </h3>
              <div className="p-3 rounded-lg bg-emerald-500/5 border border-emerald-500/20 text-sm text-slate-200 whitespace-pre-wrap max-h-40 overflow-y-auto leading-relaxed">
                {campaign.reply_body || "—"}
              </div>
            </div>

            {campaign.reply_intent && (
              <div>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Reply Intent</h3>
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-500/10 text-blue-400 border border-blue-500/20">
                  <Zap className="w-3 h-3" />
                  {campaign.reply_intent}
                </span>
              </div>
            )}

            {campaign.ai_extracted_insights && (
              <div>
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">AI Insights</h3>
                <div className="p-3 rounded-lg bg-blue-500/5 border border-blue-500/20 text-sm text-slate-200 whitespace-pre-wrap leading-relaxed">
                  {campaign.ai_extracted_insights}
                </div>
              </div>
            )}

            {campaign.buyer_profile_updated && (
              <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-500/10 px-3 py-2 rounded-lg border border-amber-500/20">
                <Info className="w-3.5 h-3.5" />
                Buyer profile was updated based on this reply
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [deals, setDeals] = useState<Deal[]>([]);
  const [buyers, setBuyers] = useState<Buyer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Toast for check replies
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  // Search & filter
  const [search, setSearch] = useState("");
  const [dealFilter, setDealFilter] = useState("");
  const [buyerFilter, setBuyerFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [touchFilter, setTouchFilter] = useState("");

  // Pagination
  const [page, setPage] = useState(0);
  const perPage = 15;

  // Modal state
  const [viewTarget, setViewTarget] = useState<Campaign | null>(null);

  // Action states
  const [sendingId, setSendingId] = useState<string | null>(null);
  const [checkingReplies, setCheckingReplies] = useState(false);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [campaignsData, dealsData, buyersData] = await Promise.all([
        apiFetch<Campaign[]>("/api/campaigns"),
        apiFetch<Deal[]>("/api/deals"),
        apiFetch<Buyer[]>("/api/buyers"),
      ]);
      setCampaigns(campaignsData);
      setDeals(dealsData);
      setBuyers(buyersData);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Auto-refresh every 60s
  usePolling(loadData, 60000);

  // Build lookup maps
  const dealMap = new Map(deals.map((d) => [d.id, d.address]));
  const buyerNameMap = new Map(buyers.map((b) => [b.id, b.full_name]));
  const buyerEmailMap = new Map(buyers.map((b) => [b.id, b.email]));

  // Build unique filter options
  const uniqueDealIds = Array.from(new Set(campaigns.map((c) => c.deal_id)));
  const uniqueBuyerIds = Array.from(new Set(campaigns.map((c) => c.buyer_id)));

  // -----------------------------------------------------------------------
  // Filtering
  // -----------------------------------------------------------------------

  const filtered = campaigns.filter((c) => {
    const dealAddr = dealMap.get(c.deal_id) || "";
    const buyerName = buyerNameMap.get(c.buyer_id) || "";

    const q = search.toLowerCase();
    if (q && !dealAddr.toLowerCase().includes(q) && !buyerName.toLowerCase().includes(q)) return false;
    if (dealFilter && c.deal_id !== dealFilter) return false;
    if (buyerFilter && c.buyer_id !== buyerFilter) return false;
    if (statusFilter && c.status !== statusFilter) return false;
    if (touchFilter && c.touch_number !== Number(touchFilter)) return false;
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * perPage, safePage * perPage + perPage);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [search, dealFilter, buyerFilter, statusFilter, touchFilter]);

  // -----------------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------------

  async function handleSend(campaign: Campaign) {
    setSendingId(campaign.id);
    setError(null);
    try {
      const result = await apiFetch<SendResult>(`/api/campaigns/${campaign.id}/send`, {
        method: "POST",
      });
      setToast({
        message: `Email sent to ${result.to_email}`,
        type: "success",
      });
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSendingId(null);
    }
  }

  async function handleCheckReplies() {
    setCheckingReplies(true);
    setError(null);
    try {
      const result = await apiFetch<CheckRepliesResult>("/api/campaigns/check-replies", {
        method: "POST",
      });
      setToast({
        message: `${result.replies_processed} ${result.replies_processed === 1 ? "reply" : "replies"} processed (${result.total_replies_found} found)`,
        type: "success",
      });
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setCheckingReplies(false);
    }
  }

  // Auto-dismiss toast
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(null), 5000);
      return () => clearTimeout(timer);
    }
  }, [toast]);

  // -----------------------------------------------------------------------
  // Render helpers
  // -----------------------------------------------------------------------

  function replyStatus(c: Campaign) {
    if (c.status === "Replied") {
      return (
        <div className="space-y-0.5">
          <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-400">
            <Reply className="w-3 h-3" />
            Replied
          </span>
          {c.reply_intent && (
            <p className="text-[10px] text-slate-500">Intent: {c.reply_intent}</p>
          )}
          {c.reply_received_at && (
            <p className="text-[10px] text-slate-600">{formatDateShort(c.reply_received_at)}</p>
          )}
        </div>
      );
    }
    if (c.status === "Sent") {
      return (
        <span className="inline-flex items-center gap-1 text-xs text-slate-500">
          <Clock className="w-3 h-3" />
          Pending
        </span>
      );
    }
    return (
      <span className="text-xs text-slate-600">—</span>
    );
  }

  function canSend(c: Campaign) {
    return c.status === "Ready" || c.status === "Queued";
  }

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Campaigns</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {filtered.length} campaign{filtered.length !== 1 ? "s" : ""}
              {filtered.length !== campaigns.length && ` (filtered from ${campaigns.length})`}
            </p>
          </div>
          <button
            onClick={handleCheckReplies}
            disabled={checkingReplies}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-500 active:bg-emerald-600 transition-colors shadow-lg shadow-emerald-600/20 disabled:opacity-50"
          >
            {checkingReplies ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Check Replies
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-6">
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

        {/* Search & filters */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[200px] max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              placeholder="Search by deal or buyer…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors min-w-[140px]"
              value={dealFilter}
              onChange={(e) => setDealFilter(e.target.value)}
            >
              <option value="">All Deals</option>
              {uniqueDealIds.map((id) => (
                <option key={id} value={id}>
                  {truncate(dealMap.get(id) || "", 35)}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors min-w-[130px]"
              value={buyerFilter}
              onChange={(e) => setBuyerFilter(e.target.value)}
            >
              <option value="">All Buyers</option>
              {uniqueBuyerIds.map((id) => (
                <option key={id} value={id}>
                  {buyerNameMap.get(id) || "Unknown"}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
            >
              <option value="">All Statuses</option>
              {CAMPAIGN_STATUSES.filter(Boolean).map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              value={touchFilter}
              onChange={(e) => setTouchFilter(e.target.value)}
            >
              <option value="">All Touches</option>
              {TOUCH_NUMBERS.filter(Boolean).map((t) => (
                <option key={t} value={t}>
                  Touch #{t}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          {(search || dealFilter || buyerFilter || statusFilter || touchFilter) && (
            <button
              onClick={() => {
                setSearch("");
                setDealFilter("");
                setBuyerFilter("");
                setStatusFilter("");
                setTouchFilter("");
              }}
              className="text-sm text-slate-400 hover:text-white transition-colors"
            >
              Clear filters
            </button>
          )}
        </div>

        {/* Table card */}
        <div className="card-glass overflow-hidden transition-all duration-300 hover:border-slate-700/50">
          {loading ? (
            <div className="flex items-center justify-center py-20">
              <Loader2 className="animate-spin h-8 w-8 text-blue-500" />
            </div>
          ) : paged.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center mb-4">
                <Mail className="w-6 h-6 text-slate-500" />
              </div>
              <p className="text-slate-400 text-sm font-medium">
                {search || dealFilter || buyerFilter || statusFilter || touchFilter
                  ? "No campaigns match your filters"
                  : "No campaigns yet"}
              </p>
              <p className="text-slate-600 text-xs mt-1">
                {search || dealFilter || buyerFilter || statusFilter || touchFilter
                  ? "Try adjusting your search or filter criteria"
                  : "Launch a campaign from the Deals page to get started"}
              </p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Deal</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Buyer</th>
                      <th className="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Touch</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Status</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Subject</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Sent At</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Reply</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {paged.map((c, i) => {
                      const dealAddr = dealMap.get(c.deal_id) || c.deal_id;
                      const bName = buyerNameMap.get(c.buyer_id) || "Unknown";
                      const bEmail = buyerEmailMap.get(c.buyer_id) || "";

                      return (
                        <tr key={c.id} className="group hover:bg-slate-800/30 transition-colors" style={{ animation: `fadeIn 0.3s ease-out ${i * 25}ms forwards`, opacity: 0 }}>
                          <td className="px-4 py-3 max-w-[180px]">
                            <span className="text-white truncate block" title={dealAddr}>
                              {truncate(dealAddr, 30)}
                            </span>
                          </td>
                          <td className="px-4 py-3 max-w-[150px]">
                            <div className="truncate">
                              <p className="text-white truncate">{bName}</p>
                              <p className="text-[10px] text-slate-500 truncate">{bEmail}</p>
                            </div>
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-slate-700/50 text-xs font-medium text-slate-300">
                              {c.touch_number}
                            </span>
                          </td>
                          <td className="px-4 py-3">
                            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${statusConfig[c.status]?.bg || ""}`}>
                              <span className={`w-1.5 h-1.5 rounded-full ${statusConfig[c.status]?.dot || ""}`} />
                              {c.status}
                            </span>
                          </td>
                          <td className="px-4 py-3 max-w-[200px] text-slate-400">
                            <span title={c.subject || ""}>{truncate(c.subject, 40)}</span>
                          </td>
                          <td className="px-4 py-3 text-slate-500 text-xs whitespace-nowrap">
                            {formatDateShort(c.sent_at)}
                          </td>
                          <td className="px-4 py-3">{replyStatus(c)}</td>
                          <td className="px-4 py-3 text-right">
                            <div className="inline-flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                              <button
                                onClick={() => setViewTarget(c)}
                                className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
                                title="View details"
                              >
                                <Eye className="w-4 h-4" />
                              </button>
                              {canSend(c) && (
                                <button
                                  onClick={() => handleSend(c)}
                                  disabled={sendingId === c.id}
                                  className="p-1.5 rounded-md text-slate-400 hover:text-blue-400 hover:bg-slate-700 transition-colors disabled:opacity-30"
                                  title="Send email"
                                >
                                  {sendingId === c.id ? (
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                  ) : (
                                    <Send className="w-4 h-4" />
                                  )}
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Pagination */}
              <div className="flex items-center justify-between px-4 py-3 border-t border-slate-800/50">
                <p className="text-xs text-slate-500">
                  Showing {safePage * perPage + 1}–{Math.min((safePage + 1) * perPage, filtered.length)} of{" "}
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

      {/* View Modal */}
      <ViewCampaignModal
        open={!!viewTarget}
        onClose={() => setViewTarget(null)}
        campaign={viewTarget}
        dealAddress={viewTarget?.deal_id ? dealMap.get(viewTarget.deal_id) || "" : ""}
        buyerName={viewTarget?.buyer_id ? buyerNameMap.get(viewTarget.buyer_id) || "" : ""}
        buyerEmail={viewTarget?.buyer_id ? buyerEmailMap.get(viewTarget.buyer_id) || "" : ""}
      />
    </div>
  );
}

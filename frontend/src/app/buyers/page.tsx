"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  Search,
  Plus,
  Eye,
  Pencil,
  Trash2,
  X,
  ChevronLeft,
  ChevronRight,
  Mail,
  AlertCircle,
  CheckCircle2,
  Clock,
  Ban,
  ChevronDown,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Buyer {
  id: string;
  full_name: string;
  email: string;
  affiliation: string | null;
  buy_box: string;
  buyer_tier: string;
  status: string;
  notes: string | null;
  email_verified: boolean;
  email_verification_status: string | null;
  has_embedding: boolean;
  engagement_score: number;
  created_at: string;
  updated_at: string;
}

interface BuyerFormData {
  full_name: string;
  email: string;
  affiliation: string;
  buy_box: string;
  buyer_tier: string;
  status: string;
  notes: string;
}

const emptyForm: BuyerFormData = {
  full_name: "",
  email: "",
  affiliation: "",
  buy_box: "",
  buyer_tier: "C-List",
  status: "Active",
  notes: "",
};

const TIERS = ["A-List", "B-List", "C-List"];
const STATUSES = ["Active", "Paused", "Do Not Contact"];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const statusConfig: Record<string, { label: string; bg: string; dot: string }> = {
  Active: {
    label: "Active",
    bg: "bg-emerald-500/10 text-emerald-400",
    dot: "bg-emerald-400",
  },
  Paused: {
    label: "Paused",
    bg: "bg-amber-500/10 text-amber-400",
    dot: "bg-amber-400",
  },
  "Do Not Contact": {
    label: "Do Not Contact",
    bg: "bg-red-500/10 text-red-400",
    dot: "bg-red-400",
  },
};

const tierConfig: Record<string, { bg: string }> = {
  "A-List": { bg: "bg-purple-500/10 text-purple-400" },
  "B-List": { bg: "bg-blue-500/10 text-blue-400" },
  "C-List": { bg: "bg-slate-500/10 text-slate-400" },
};

function truncate(text: string, max: number) {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function getVerificationIcon(status: string | null) {
  switch (status) {
    case "valid":
      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />;
    case "invalid":
      return <AlertCircle className="w-3.5 h-3.5 text-red-400" />;
    case "catch_all":
      return <Mail className="w-3.5 h-3.5 text-amber-400" />;
    default:
      return <Clock className="w-3.5 h-3.5 text-slate-500" />;
  }
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
// Delete confirmation
// ---------------------------------------------------------------------------

function DeleteConfirm({
  open,
  onClose,
  name,
  onConfirm,
  deleting,
}: {
  open: boolean;
  onClose: () => void;
  name: string;
  onConfirm: () => void;
  deleting: boolean;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Delete Buyer">
      <div className="space-y-4">
        <p className="text-slate-300">
          Are you sure you want to delete <span className="font-semibold text-white">{name}</span>?
          This action cannot be undone.
        </p>
        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            disabled={deleting}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={deleting}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-red-600 hover:bg-red-500 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {deleting ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Deleting…
              </>
            ) : (
              "Delete"
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Buyer form (shared between Add & Edit)
// ---------------------------------------------------------------------------

function BuyerForm({
  data,
  onChange,
  errors,
}: {
  data: BuyerFormData;
  onChange: (d: BuyerFormData) => void;
  errors: Partial<Record<keyof BuyerFormData, string>>;
}) {
  const field =
    (key: keyof BuyerFormData) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      onChange({ ...data, [key]: e.target.value });

  const inputCls =
    "w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors";
  const labelCls = "block text-sm font-medium text-slate-400 mb-1.5";
  const errorCls = "text-xs text-red-400 mt-1";

  return (
    <div className="space-y-4">
      {/* Full Name */}
      <div>
        <label className={labelCls}>
          Full Name <span className="text-red-400">*</span>
        </label>
        <input className={inputCls} value={data.full_name} onChange={field("full_name")} placeholder="e.g. John Smith" />
        {errors.full_name && <p className={errorCls}>{errors.full_name}</p>}
      </div>

      {/* Email */}
      <div>
        <label className={labelCls}>
          Email <span className="text-red-400">*</span>
        </label>
        <input className={inputCls} value={data.email} onChange={field("email")} placeholder="e.g. john@example.com" type="email" />
        {errors.email && <p className={errorCls}>{errors.email}</p>}
      </div>

      {/* Affiliation */}
      <div>
        <label className={labelCls}>Affiliation</label>
        <input className={inputCls} value={data.affiliation} onChange={field("affiliation")} placeholder="e.g. ABC Realty" />
      </div>

      {/* Buy Box */}
      <div>
        <label className={labelCls}>
          Buy Box <span className="text-red-400">*</span>
        </label>
        <textarea
          className={`${inputCls} min-h-[80px] resize-y`}
          value={data.buy_box}
          onChange={field("buy_box")}
          placeholder="Describe what this buyer is looking for…"
        />
        {errors.buy_box && <p className={errorCls}>{errors.buy_box}</p>}
      </div>

      {/* Tier & Status row */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>Buyer Tier</label>
          <div className="relative">
            <select className={`${inputCls} appearance-none`} value={data.buyer_tier} onChange={field("buyer_tier")}>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>
        </div>
        <div>
          <label className={labelCls}>Status</label>
          <div className="relative">
            <select className={`${inputCls} appearance-none`} value={data.status} onChange={field("status")}>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>
        </div>
      </div>

      {/* Notes */}
      <div>
        <label className={labelCls}>Notes</label>
        <textarea
          className={`${inputCls} min-h-[60px] resize-y`}
          value={data.notes}
          onChange={field("notes")}
          placeholder="Optional notes…"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// View modal
// ---------------------------------------------------------------------------

function ViewModal({
  open,
  onClose,
  buyer,
}: {
  open: boolean;
  onClose: () => void;
  buyer: Buyer | null;
}) {
  if (!buyer) return null;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start gap-4 py-2.5 border-b border-slate-700/30 last:border-0">
      <span className="w-32 shrink-0 text-sm text-slate-500">{label}</span>
      <span className="text-sm text-white break-words">{value}</span>
    </div>
  );

  return (
    <Modal open={open} onClose={onClose} title="Buyer Details">
      <div className="divide-y divide-slate-700/30">
        <Row label="Name" value={buyer.full_name} />
        <Row
          label="Email"
          value={
            <span className="inline-flex items-center gap-1.5">
              {buyer.email}
              {getVerificationIcon(buyer.email_verification_status)}
            </span>
          }
        />
        <Row label="Affiliation" value={buyer.affiliation || "—"} />
        <Row
          label="Buy Box"
          value={<div className="whitespace-pre-wrap">{buyer.buy_box}</div>}
        />
        <Row
          label="Tier"
          value={
            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${tierConfig[buyer.buyer_tier]?.bg || ""}`}>
              {buyer.buyer_tier}
            </span>
          }
        />
        <Row
          label="Status"
          value={
            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${statusConfig[buyer.status]?.bg || ""}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${statusConfig[buyer.status]?.dot || ""}`} />
              {buyer.status}
            </span>
          }
        />
        <Row
          label="Engagement"
          value={`${buyer.engagement_score.toFixed(1)}%`}
        />
        <Row
          label="Email Verified"
          value={
            buyer.email_verified ? (
              <span className="text-emerald-400 inline-flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> {buyer.email_verification_status || "Yes"}
              </span>
            ) : (
              <span className="text-slate-400 inline-flex items-center gap-1">
                {getVerificationIcon(buyer.email_verification_status)}
                {buyer.email_verification_status || "Pending"}
              </span>
            )
          }
        />
        <Row
          label="Matchable"
          value={
            buyer.has_embedding ? (
              <span className="text-emerald-400 inline-flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> Yes (embedded)
              </span>
            ) : (
              <span className="text-amber-400 inline-flex items-center gap-1">
                <Clock className="w-3.5 h-3.5" /> {buyer.email_verified ? "Pending embed" : "Needs verification first"}
              </span>
            )
          }
        />
        <Row label="Notes" value={buyer.notes || "—"} />
        <Row label="Created" value={formatDate(buyer.created_at)} />
        <Row label="Updated" value={formatDate(buyer.updated_at)} />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function BuyersPage() {
  const [buyers, setBuyers] = useState<Buyer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Search & filter
  const [search, setSearch] = useState("");
  const [tierFilter, setTierFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [verificationFilter, setVerificationFilter] = useState("");

  // Pagination
  const [page, setPage] = useState(0);
  const perPage = 10;

  // Modal state
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Buyer | null>(null);
  const [viewTarget, setViewTarget] = useState<Buyer | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Buyer | null>(null);

  // Form state
  const [form, setForm] = useState<BuyerFormData>(emptyForm);
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof BuyerFormData, string>>>({});
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadBuyers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<Buyer[]>("/api/buyers");
      setBuyers(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadBuyers();
  }, [loadBuyers]);

  // Auto-refresh every 60s
  usePolling(loadBuyers, 60000);

  // -----------------------------------------------------------------------
  // Filtering
  // -----------------------------------------------------------------------

  const filtered = buyers.filter((b) => {
    const q = search.toLowerCase();
    if (q && !b.full_name.toLowerCase().includes(q) && !b.email.toLowerCase().includes(q)) return false;
    if (tierFilter && b.buyer_tier !== tierFilter) return false;
    if (statusFilter && b.status !== statusFilter) return false;
    if (verificationFilter === "verified" && !b.email_verified) return false;
    if (verificationFilter === "unverified" && b.email_verified) return false;
    if (verificationFilter === "invalid" && b.email_verification_status !== "invalid") return false;
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * perPage, safePage * perPage + perPage);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [search, tierFilter, statusFilter, verificationFilter]);

  // -----------------------------------------------------------------------
  // Validation
  // -----------------------------------------------------------------------

  function validate(data: BuyerFormData): Partial<Record<keyof BuyerFormData, string>> {
    const errs: Partial<Record<keyof BuyerFormData, string>> = {};
    if (!data.full_name.trim()) errs.full_name = "Name is required";
    if (!data.email.trim()) errs.email = "Email is required";
    else if (!/\S+@\S+\.\S+/.test(data.email)) errs.email = "Invalid email format";
    if (!data.buy_box.trim()) errs.buy_box = "Buy box is required";
    return errs;
  }

  // -----------------------------------------------------------------------
  // CRUD
  // -----------------------------------------------------------------------

  async function handleCreate() {
    const errs = validate(form);
    setFormErrors(errs);
    if (Object.keys(errs).length) return;

    setSaving(true);
    try {
      await apiFetch("/api/buyers", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setAddOpen(false);
      setForm(emptyForm);
      setFormErrors({});
      await loadBuyers();
    } catch (err: any) {
      setFormErrors({ email: err.message });
    } finally {
      setSaving(false);
    }
  }

  async function handleUpdate() {
    if (!editTarget) return;
    const errs = validate(form);
    setFormErrors(errs);
    if (Object.keys(errs).length) return;

    setSaving(true);
    try {
      await apiFetch(`/api/buyers/${editTarget.id}`, {
        method: "PUT",
        body: JSON.stringify(form),
      });
      setEditTarget(null);
      setForm(emptyForm);
      setFormErrors({});
      await loadBuyers();
    } catch (err: any) {
      setFormErrors({ email: err.message });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiFetch(`/api/buyers/${deleteTarget.id}`, {
        method: "DELETE",
      });
      setDeleteTarget(null);
      await loadBuyers();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  function openEdit(b: Buyer) {
    setEditTarget(b);
    setForm({
      full_name: b.full_name,
      email: b.email,
      affiliation: b.affiliation || "",
      buy_box: b.buy_box,
      buyer_tier: b.buyer_tier,
      status: b.status,
      notes: b.notes || "",
    });
    setFormErrors({});
  }

  function openAdd() {
    setForm(emptyForm);
    setFormErrors({});
    setAddOpen(true);
  }

  // -----------------------------------------------------------------------
  // Scoring color
  // -----------------------------------------------------------------------

  function scoreColor(score: number) {
    if (score >= 70) return "text-emerald-400";
    if (score >= 40) return "text-amber-400";
    return "text-red-400";
  }

  function scoreBar(score: number) {
    const clamped = Math.min(100, Math.max(0, score));
    let color = "bg-emerald-500";
    if (clamped < 70) color = "bg-amber-500";
    if (clamped < 40) color = "bg-red-500";
    return (
      <div className="flex items-center gap-2">
        <div className="w-16 h-1.5 rounded-full bg-slate-700 overflow-hidden">
          <div className={`h-full rounded-full ${color}`} style={{ width: `${clamped}%` }} />
        </div>
        <span className={`text-xs font-medium tabular-nums ${scoreColor(clamped)}`}>
          {clamped.toFixed(0)}%
        </span>
      </div>
    );
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
            <h1 className="text-2xl font-bold text-white tracking-tight">Buyers</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {filtered.length} buyer{filtered.length !== 1 ? "s" : ""}
              {filtered.length !== buyers.length && ` (filtered from ${buyers.length})`}
            </p>
          </div>
          <button
            onClick={openAdd}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 active:bg-blue-600 transition-colors shadow-lg shadow-blue-600/20"
          >
            <Plus className="w-4 h-4" />
            Add Buyer
          </button>
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
              placeholder="Search by name or email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value)}
            >
              <option value="">All Tiers</option>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
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
              {STATUSES.map((s) => (
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
              value={verificationFilter}
              onChange={(e) => setVerificationFilter(e.target.value)}
            >
              <option value="">All Emails</option>
              <option value="verified">Verified</option>
              <option value="unverified">Unverified</option>
              <option value="invalid">Invalid</option>
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          {(search || tierFilter || statusFilter || verificationFilter) && (
            <button
              onClick={() => {
                setSearch("");
                setTierFilter("");
                setStatusFilter("");
                setVerificationFilter("");
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
              <svg className="animate-spin h-8 w-8 text-blue-500" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            </div>
          ) : paged.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center mb-4">
                <Search className="w-6 h-6 text-slate-500" />
              </div>
              <p className="text-slate-400 text-sm font-medium">
                {search || tierFilter || statusFilter ? "No buyers match your filters" : "No buyers yet"}
              </p>
              <p className="text-slate-600 text-xs mt-1">
                {search || tierFilter || statusFilter
                  ? "Try adjusting your search or filter criteria"
                  : "Add your first buyer to get started"}
              </p>
            </div>
          ) : (
            <>
              {/* Table - horizontal scroll on small screens */}
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Name</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Email</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Tier</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Status</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Buy Box</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Engagement</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {paged.map((b, i) => (
                      <tr
                        key={b.id}
                        className="group hover:bg-slate-800/30 transition-colors"
                        style={{ animation: `fadeIn 0.3s ease-out ${i * 30}ms forwards`, opacity: 0 }}
                      >
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center text-xs font-semibold text-blue-400">
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
                        <td className="px-4 py-3">
                          <span className="inline-flex items-center gap-1.5 text-slate-300">
                            {b.email}
                            {getVerificationIcon(b.email_verification_status)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${tierConfig[b.buyer_tier]?.bg || ""}`}>
                            {b.buyer_tier}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${statusConfig[b.status]?.bg || ""}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${statusConfig[b.status]?.dot || ""}`} />
                            {b.status === "Do Not Contact" ? (
                              <span className="flex items-center gap-1">
                                <Ban className="w-3 h-3" /> {b.status}
                              </span>
                            ) : (
                              b.status
                            )}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-slate-400 max-w-[200px]">
                          <span title={b.buy_box}>{truncate(b.buy_box, 60)}</span>
                        </td>
                        <td className="px-4 py-3">{scoreBar(b.engagement_score)}</td>
                        <td className="px-4 py-3 text-right">
                          <div className="inline-flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={() => setViewTarget(b)}
                              className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
                              title="View details"
                            >
                              <Eye className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => openEdit(b)}
                              className="p-1.5 rounded-md text-slate-400 hover:text-blue-400 hover:bg-slate-700 transition-colors"
                              title="Edit buyer"
                            >
                              <Pencil className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => setDeleteTarget(b)}
                              className="p-1.5 rounded-md text-slate-400 hover:text-red-400 hover:bg-slate-700 transition-colors"
                              title="Delete buyer"
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
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

      {/* Add Modal */}
      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="Add Buyer">
        <BuyerForm data={form} onChange={setForm} errors={formErrors} />
        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-slate-700/50">
          <button
            onClick={() => setAddOpen(false)}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {saving ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Saving…
              </>
            ) : (
              "Create Buyer"
            )}
          </button>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal open={!!editTarget} onClose={() => { setEditTarget(null); setForm(emptyForm); }} title="Edit Buyer">
        <BuyerForm data={form} onChange={setForm} errors={formErrors} />
        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-slate-700/50">
          <button
            onClick={() => { setEditTarget(null); setForm(emptyForm); }}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleUpdate}
            disabled={saving}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {saving ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Saving…
              </>
            ) : (
              "Save Changes"
            )}
          </button>
        </div>
      </Modal>

      {/* View Modal */}
      <ViewModal open={!!viewTarget} onClose={() => setViewTarget(null)} buyer={viewTarget} />

      {/* Delete Confirm */}
      <DeleteConfirm
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        name={deleteTarget?.full_name || ""}
        onConfirm={handleDelete}
        deleting={deleting}
      />
    </div>
  );
}

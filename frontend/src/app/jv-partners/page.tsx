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
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  DollarSign,
  Users,
  TrendingUp,
  Mail,
  Star,
  Flag,
} from "lucide-react";
import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface JVPartner {
  id: string;
  name: string;
  email: string;
  phone: string | null;
  source: string | null;
  deals_linked: string[];
  total_deals_submitted: number;
  total_deals_closed: number;
  total_revenue_generated: number;
  avg_buyer_feedback_score: number;
  title_issue_rate: number;
  overprice_flag_count: number;
  total_split_revenue: number;
  created_at: string;
}

interface JVFormData {
  name: string;
  email: string;
  phone: string;
  source: string;
}

const emptyForm: JVFormData = {
  name: "",
  email: "",
  phone: "",
  source: "",
};

// ---------------------------------------------------------------------------
// Helpers
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

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatPercent(val: number): string {
  return `${(val * 100).toFixed(0)}%`;
}

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
    <Modal open={open} onClose={onClose} title="Delete JV Partner">
      <div className="space-y-4">
        <p className="text-slate-300">
          Are you sure you want to delete{" "}
          <span className="font-semibold text-white">{name}</span>?
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
              <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>Deleting&hellip;</>
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
// JV Partner form
// ---------------------------------------------------------------------------

function JVForm({
  data,
  onChange,
  errors,
}: {
  data: JVFormData;
  onChange: (d: JVFormData) => void;
  errors: Partial<Record<keyof JVFormData, string>>;
}) {
  const field =
    (key: keyof JVFormData) =>
    (e: React.ChangeEvent<HTMLInputElement>) =>
      onChange({ ...data, [key]: e.target.value });

  const inputCls =
    "w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors";
  const labelCls = "block text-sm font-medium text-slate-400 mb-1.5";
  const errorCls = "text-xs text-red-400 mt-1";

  return (
    <div className="space-y-4">
      <div>
        <label className={labelCls}>
          Name <span className="text-red-400">*</span>
        </label>
        <input className={inputCls} value={data.name} onChange={field("name")} placeholder="e.g. John Smith" />
        {errors.name && <p className={errorCls}>{errors.name}</p>}
      </div>
      <div>
        <label className={labelCls}>
          Email <span className="text-red-400">*</span>
        </label>
        <input className={inputCls} value={data.email} onChange={field("email")} placeholder="e.g. john@example.com" type="email" />
        {errors.email && <p className={errorCls}>{errors.email}</p>}
      </div>
      <div>
        <label className={labelCls}>Phone</label>
        <input className={inputCls} value={data.phone} onChange={field("phone")} placeholder="e.g. (555) 123-4567" type="tel" />
      </div>
      <div>
        <label className={labelCls}>Source</label>
        <input className={inputCls} value={data.source} onChange={field("source")} placeholder="e.g. Referral, Website, Cold Call" />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Detail modal
// ---------------------------------------------------------------------------

function DetailModal({
  open,
  onClose,
  partner,
}: {
  open: boolean;
  onClose: () => void;
  partner: JVPartner | null;
}) {
  if (!partner) return null;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start gap-4 py-2.5 border-b border-slate-700/30 last:border-0">
      <span className="w-36 shrink-0 text-sm text-slate-500">{label}</span>
      <span className="text-sm text-white break-words">{value}</span>
    </div>
  );

  return (
    <Modal open={open} onClose={onClose} title="JV Partner Details">
      <div className="divide-y divide-slate-700/30">
        <Row label="Name" value={partner.name} />
        <Row label="Email" value={partner.email} />
        <Row label="Phone" value={partner.phone || "—"} />
        <Row label="Source" value={partner.source || "—"} />
        <Row label="Deals Submitted" value={partner.total_deals_submitted} />
        <Row label="Deals Closed" value={partner.total_deals_closed} />
        <Row
          label="Close Rate"
          value={
            <span className="text-emerald-400 font-medium">
              {partner.total_deals_submitted > 0
                ? Math.round((partner.total_deals_closed / partner.total_deals_submitted) * 100)
                : 0}%
            </span>
          }
        />
        <Row label="Revenue Generated" value={formatCurrency(partner.total_revenue_generated)} />
        <Row label="Split Revenue" value={formatCurrency(partner.total_split_revenue)} />
        <Row label="Avg Feedback Score" value={partner.avg_buyer_feedback_score.toFixed(1)} />
        <Row label="Title Issue Rate" value={formatPercent(partner.title_issue_rate)} />
        <Row label="Overprice Flags" value={partner.overprice_flag_count} />
        <Row label="Deals Linked" value={partner.deals_linked.length} />
        <Row label="Created" value={formatDate(partner.created_at)} />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Score card for stats
// ---------------------------------------------------------------------------

function StatCard({ title, value, icon, color = "blue" }: {
  title: string;
  value: string;
  icon: React.ReactNode;
  color?: string;
}) {
  const colorMap: Record<string, string> = {
    blue: "bg-blue-500/10 text-blue-400",
    emerald: "bg-emerald-500/10 text-emerald-400",
    amber: "bg-amber-500/10 text-amber-400",
    purple: "bg-purple-500/10 text-purple-400",
    cyan: "bg-cyan-500/10 text-cyan-400",
  };
  return (
    <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 transition-all duration-300 hover:border-slate-700/50 hover:bg-slate-800/50">
      <div className={`w-9 h-9 rounded-lg flex items-center justify-center mb-3 ${colorMap[color]}`}>
        {icon}
      </div>
      <p className="text-2xl font-bold text-white tracking-tight tabular-nums">{value}</p>
      <p className="text-xs text-slate-500 mt-0.5">{title}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function JVPartnersPage() {
  const [partners, setPartners] = useState<JVPartner[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Search
  const [search, setSearch] = useState("");

  // Pagination
  const [page, setPage] = useState(0);
  const perPage = 10;

  // Modal state
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<JVPartner | null>(null);
  const [viewTarget, setViewTarget] = useState<JVPartner | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<JVPartner | null>(null);

  // Form state
  const [form, setForm] = useState<JVFormData>(emptyForm);
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof JVFormData, string>>>({});
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadPartners = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<JVPartner[]>("/api/jv-partners");
      setPartners(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPartners();
  }, [loadPartners]);

  // Auto-refresh every 60s
  usePolling(loadPartners, 60000);

  // -----------------------------------------------------------------------
  // Filtering
  // -----------------------------------------------------------------------

  const filtered = partners.filter((p) => {
    const q = search.toLowerCase();
    if (q && !p.name.toLowerCase().includes(q) && !p.email.toLowerCase().includes(q)) return false;
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * perPage, safePage * perPage + perPage);

  useEffect(() => {
    setPage(0);
  }, [search]);

  // -----------------------------------------------------------------------
  // Stats
  // -----------------------------------------------------------------------

  const totalRevenue = partners.reduce((s, p) => s + p.total_revenue_generated, 0);
  const totalClosed = partners.reduce((s, p) => s + p.total_deals_closed, 0);
  const totalSubmitted = partners.reduce((s, p) => s + p.total_deals_submitted, 0);
  const avgCloseRate = totalSubmitted > 0 ? Math.round((totalClosed / totalSubmitted) * 100) : 0;

  // -----------------------------------------------------------------------
  // Validation
  // -----------------------------------------------------------------------

  function validate(data: JVFormData): Partial<Record<keyof JVFormData, string>> {
    const errs: Partial<Record<keyof JVFormData, string>> = {};
    if (!data.name.trim()) errs.name = "Name is required";
    if (!data.email.trim()) errs.email = "Email is required";
    else if (!/\S+@\S+\.\S+/.test(data.email)) errs.email = "Invalid email format";
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
      await apiFetch("/api/jv-partners", {
        method: "POST",
        body: JSON.stringify(form),
      });
      setAddOpen(false);
      setForm(emptyForm);
      setFormErrors({});
      await loadPartners();
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
      await apiFetch(`/api/jv-partners/${editTarget.id}`, {
        method: "PUT",
        body: JSON.stringify(form),
      });
      setEditTarget(null);
      setForm(emptyForm);
      setFormErrors({});
      await loadPartners();
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
      await apiFetch(`/api/jv-partners/${deleteTarget.id}`, {
        method: "DELETE",
      });
      setDeleteTarget(null);
      await loadPartners();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  function openEdit(p: JVPartner) {
    setEditTarget(p);
    setForm({ name: p.name, email: p.email, phone: p.phone || "", source: p.source || "" });
    setFormErrors({});
  }

  function openAdd() {
    setForm(emptyForm);
    setFormErrors({});
    setAddOpen(true);
  }

  // -----------------------------------------------------------------------
  // Render helpers
  // -----------------------------------------------------------------------

  function initials(name: string) {
    return name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase();
  }

  function closeRateColor(p: JVPartner) {
    const rate = p.total_deals_submitted > 0 ? (p.total_deals_closed / p.total_deals_submitted) * 100 : 0;
    if (rate >= 50) return "text-emerald-400";
    if (rate >= 25) return "text-amber-400";
    return "text-red-400";
  }

  function feedbackColor(score: number) {
    if (score >= 4) return "text-emerald-400";
    if (score >= 3) return "text-amber-400";
    return "text-red-400";
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
            <h1 className="text-2xl font-bold text-white tracking-tight">JV Partners</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {filtered.length} partner{filtered.length !== 1 ? "s" : ""}
              {filtered.length !== partners.length && ` (filtered from ${partners.length})`}
            </p>
          </div>
          <button
            onClick={openAdd}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 active:bg-blue-600 transition-colors shadow-lg shadow-blue-600/20"
          >
            <Plus className="w-4 h-4" />
            Add JV Partner
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

        {/* Stats row */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard
            title="Partners"
            value={partners.length.toString()}
            icon={<Users className="w-4 h-4" />}
            color="blue"
          />
          <StatCard
            title="Deals Submitted"
            value={totalSubmitted.toString()}
            icon={<TrendingUp className="w-4 h-4" />}
            color="emerald"
          />
          <StatCard
            title="Deals Closed"
            value={totalClosed.toString()}
            icon={<CheckCircle2 className="w-4 h-4" />}
            color="purple"
          />
          <StatCard
            title="Revenue Generated"
            value={formatCurrency(totalRevenue)}
            icon={<DollarSign className="w-4 h-4" />}
            color="amber"
          />
        </div>

        {/* Search */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="relative flex-1 min-w-[240px] max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
            <input
              className="w-full pl-9 pr-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              placeholder="Search by name or email&hellip;"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          {search && (
            <button
              onClick={() => setSearch("")}
              className="text-sm text-slate-400 hover:text-white transition-colors"
            >
              Clear
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
                <Users className="w-6 h-6 text-slate-500" />
              </div>
              <p className="text-slate-400 text-sm font-medium">
                {search ? "No partners match your search" : "No JV partners yet"}
              </p>
              <p className="text-slate-600 text-xs mt-1">
                {search ? "Try adjusting your search" : "Add your first JV partner to get started"}
              </p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Name</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Email</th>
                      <th className="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Deals</th>
                      <th className="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Close Rate</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Revenue</th>
                      <th className="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Feedback</th>
                      <th className="text-center px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Flags</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {paged.map((p, i) => {
                      const closeRate = p.total_deals_submitted > 0
                        ? Math.round((p.total_deals_closed / p.total_deals_submitted) * 100)
                        : 0;
                      return (
                        <tr
                          key={p.id}
                          className="group hover:bg-slate-800/30 transition-colors"
                          style={{ animation: `fadeIn 0.3s ease-out ${i * 30}ms forwards`, opacity: 0 }}
                        >
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-cyan-500/20 to-blue-500/20 flex items-center justify-center text-xs font-semibold text-cyan-400">
                                {initials(p.name)}
                              </div>
                              <span className="font-medium text-white">{p.name}</span>
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            <span className="text-slate-300">{p.email}</span>
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span className="inline-flex items-center gap-1.5 text-xs">
                              <span className="font-medium text-white">{p.total_deals_closed}</span>
                              <span className="text-slate-600">/ {p.total_deals_submitted}</span>
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span className={`text-xs font-semibold tabular-nums ${closeRateColor(p)}`}>
                              {closeRate}%
                            </span>
                          </td>
                          <td className="px-4 py-3 text-right text-slate-300 tabular-nums font-medium">
                            {formatCurrency(p.total_revenue_generated)}
                          </td>
                          <td className="px-4 py-3 text-center">
                            <span className={`inline-flex items-center gap-1 text-xs font-medium ${feedbackColor(p.avg_buyer_feedback_score)}`}>
                              <Star className="w-3 h-3" />
                              {p.avg_buyer_feedback_score.toFixed(1)}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-center">
                            {p.overprice_flag_count > 0 ? (
                              <span className="inline-flex items-center gap-1 text-xs text-red-400">
                                <Flag className="w-3 h-3" />
                                {p.overprice_flag_count}
                              </span>
                            ) : (
                              <span className="text-xs text-slate-600">&mdash;</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right">
                            <div className="inline-flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                              <button
                                onClick={() => setViewTarget(p)}
                                className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
                                title="View details"
                              >
                                <Eye className="w-4 h-4" />
                              </button>
                              <button
                                onClick={() => openEdit(p)}
                                className="p-1.5 rounded-md text-slate-400 hover:text-blue-400 hover:bg-slate-700 transition-colors"
                                title="Edit partner"
                              >
                                <Pencil className="w-4 h-4" />
                              </button>
                              <button
                                onClick={() => setDeleteTarget(p)}
                                className="p-1.5 rounded-md text-slate-400 hover:text-red-400 hover:bg-slate-700 transition-colors"
                                title="Delete partner"
                              >
                                <Trash2 className="w-4 h-4" />
                              </button>
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
                  Showing {safePage * perPage + 1}&ndash;{Math.min((safePage + 1) * perPage, filtered.length)} of {filtered.length}
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
      <Modal open={addOpen} onClose={() => setAddOpen(false)} title="Add JV Partner">
        <JVForm data={form} onChange={setForm} errors={formErrors} />
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
              <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>Saving&hellip;</>
            ) : (
              "Create Partner"
            )}
          </button>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal open={!!editTarget} onClose={() => { setEditTarget(null); setForm(emptyForm); }} title="Edit JV Partner">
        <JVForm data={form} onChange={setForm} errors={formErrors} />
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
              <><svg className="animate-spin h-4 w-4" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" /><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" /></svg>Saving&hellip;</>
            ) : (
              "Save Changes"
            )}
          </button>
        </div>
      </Modal>

      {/* View Modal */}
      <DetailModal open={!!viewTarget} onClose={() => setViewTarget(null)} partner={viewTarget} />

      {/* Delete Confirm */}
      <DeleteConfirm
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        name={deleteTarget?.name || ""}
        onConfirm={handleDelete}
        deleting={deleting}
      />
    </div>
  );
}

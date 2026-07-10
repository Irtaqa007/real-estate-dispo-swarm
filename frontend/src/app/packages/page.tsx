"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import {
  Package, Plus, X, DollarSign, TrendingUp, Calendar,
  ChevronDown, ChevronUp, Play, CheckCircle, Trash2,
  AlertCircle, Building2, Users, Send, ArrowLeft
} from "lucide-react";

interface PackageItem {
  id: string;
  name: string;
  package_price: number;
  package_arv: number | null;
  floor_price: number;
  status: string;
  description: string | null;
  expiry_date: string | null;
  created_at: string;
  deals: PackageDeal[];
  total_individual_price: number;
  savings: number;
  campaign_stats: { total: number; sent: number; replied: number; passed: number } | null;
}

interface PackageDeal {
  deal_id: string;
  address: string;
  city: string | null;
  state: string | null;
  property_type: string;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  asking_price: number;
  arv: number;
}

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : "$" + n.toLocaleString();

const statusBadge: Record<string, string> = {
  Active: "bg-blue-500/20 text-blue-300 border-blue-500/30",
  Launched: "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
  Sold: "bg-amber-500/20 text-amber-300 border-amber-500/30",
  Expired: "bg-slate-500/20 text-slate-400 border-slate-500/30",
};

export default function PackagesPage() {
  const [packages, setPackages] = useState<PackageItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [selectedPkg, setSelectedPkg] = useState<PackageItem | null>(null);
  const [deals, setDeals] = useState<any[]>([]);

  const [form, setForm] = useState({
    name: "", deal_ids: [] as string[], package_price: "",
    floor_price: "", description: "", expiry_date: "",
  });
  const [creating, setCreating] = useState(false);

  async function loadPackages() {
    try {
      const data = await apiFetch<PackageItem[]>("/api/packages");
      setPackages(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }

  async function loadDeals() {
    try {
      const data = await apiFetch<any[]>("/api/deals?limit=200");
      setDeals(data);
    } catch {}
  }

  useEffect(() => {
    loadPackages();
    loadDeals();
  }, []);

  async function handleCreate() {
    if (!form.name || !form.package_price || !form.floor_price || form.deal_ids.length < 2) return;
    setCreating(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        deal_ids: form.deal_ids.map(id => id),
        package_price: parseFloat(form.package_price),
        floor_price: parseFloat(form.floor_price),
      };
      if (form.description) payload.description = form.description;
      if (form.expiry_date) payload.expiry_date = new Date(form.expiry_date).toISOString();

      await apiFetch("/api/packages", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setShowCreate(false);
      setForm({ name: "", deal_ids: [], package_price: "", floor_price: "", description: "", expiry_date: "" });
      loadPackages();
    } catch (e: any) {
      alert(e.message || "Failed to create package");
    } finally {
      setCreating(false);
    }
  }

  async function handleLaunch(pkgId: string) {
    try {
      await apiFetch(`/api/packages/${pkgId}/launch`, { method: "POST" });
      loadPackages();
    } catch (e: any) {
      alert(e.message || "Failed to launch package");
    }
  }

  async function handleClose(pkgId: string) {
    if (!confirm("Mark this package as Sold? Individual deal statuses will NOT change.")) return;
    try {
      await apiFetch(`/api/packages/${pkgId}/close`, { method: "POST" });
      loadPackages();
    } catch (e: any) {
      alert(e.message || "Failed to close package");
    }
  }

  async function handleDelete(pkgId: string) {
    if (!confirm("Delete this package? Only Active packages can be deleted.")) return;
    try {
      await apiFetch(`/api/packages/${pkgId}`, { method: "DELETE" });
      loadPackages();
    } catch (e: any) {
      alert(e.message || "Failed to delete package");
    }
  }

  const selectedDeals = deals.filter(d => form.deal_ids.includes(d.id));
  const totalIndiv = selectedDeals.reduce((sum, d) => sum + (d.asking_price || 0), 0);
  const pkgPrice = parseFloat(form.package_price) || 0;
  const savings = totalIndiv - pkgPrice;

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="max-w-5xl mx-auto px-6 py-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Package className="w-5 h-5 text-blue-400" />
            Packages
          </h1>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            Create Package
          </button>
        </div>

        {/* Loading */}
        {loading && <p className="text-sm text-slate-500">Loading packages...</p>}

        {/* Package List */}
        {!loading && packages.length === 0 && (
          <div className="text-center py-12">
            <Package className="w-12 h-12 text-slate-700 mx-auto mb-3" />
            <p className="text-sm text-slate-500">No packages yet.</p>
            <p className="text-xs text-slate-600 mt-1">Group 2-5 deals into a bundle and pitch them at a discount.</p>
          </div>
        )}

        <div className="space-y-3">
          {packages.map(pkg => (
            <div key={pkg.id} className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
              {/* Header row */}
              <div className="flex items-center justify-between px-4 py-3">
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-medium text-white">{pkg.name}</h3>
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${statusBadge[pkg.status] || "bg-slate-700 text-slate-400"}`}>
                    {pkg.status}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {pkg.status === "Active" && (
                    <>
                      <button
                        onClick={() => handleLaunch(pkg.id)}
                        className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium text-emerald-300 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 transition-colors"
                      >
                        <Play className="w-3 h-3" />
                        Launch
                      </button>
                      <button
                        onClick={() => handleDelete(pkg.id)}
                        className="p-1.5 rounded-lg text-xs text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                        title="Delete"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  )}
                  {pkg.status === "Launched" && (
                    <button
                      onClick={() => handleClose(pkg.id)}
                      className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 transition-colors"
                    >
                      <CheckCircle className="w-3 h-3" />
                      Mark Sold
                    </button>
                  )}
                  <button onClick={() => setSelectedPkg(selectedPkg?.id === pkg.id ? null : pkg)}>
                    {selectedPkg?.id === pkg.id ? <ChevronUp className="w-4 h-4 text-slate-500" /> : <ChevronDown className="w-4 h-4 text-slate-500" />}
                  </button>
                </div>
              </div>

              {/* Summary row */}
              <div className="px-4 pb-3 flex items-center gap-4 text-xs text-slate-400">
                <span>{pkg.deals.length} deals</span>
                <span className="text-emerald-400">{fmt(pkg.package_price)}</span>
                <span className="text-blue-400">Savings: {fmt(pkg.savings)}</span>
                {pkg.campaign_stats && <span>{pkg.campaign_stats.total} campaigns</span>}
              </div>

              {/* Detail view */}
              {selectedPkg?.id === pkg.id && (
                <div className="px-4 pb-4 pt-2 border-t border-slate-800 space-y-3">
                  {/* Deals */}
                  <div>
                    <p className="text-xs font-medium text-slate-500 mb-2">Included Deals</p>
                    <div className="space-y-1.5">
                      {pkg.deals.map(d => (
                        <Link key={d.deal_id} href={`/deals/${d.deal_id}`}
                          className="flex items-center justify-between px-3 py-2 rounded-lg bg-slate-800/30 hover:bg-slate-800/60 transition-colors text-xs">
                          <span className="text-slate-300">{d.address}</span>
                          <span className="text-slate-400">{fmt(d.asking_price)}</span>
                        </Link>
                      ))}
                    </div>
                  </div>

                  {/* Financial summary */}
                  <div className="grid grid-cols-3 gap-2">
                    <div className="bg-slate-800/30 rounded-lg p-2.5">
                      <p className="text-[10px] text-slate-500">Package Price</p>
                      <p className="text-sm font-semibold text-emerald-400">{fmt(pkg.package_price)}</p>
                    </div>
                    <div className="bg-slate-800/30 rounded-lg p-2.5">
                      <p className="text-[10px] text-slate-500">Combined ARV</p>
                      <p className="text-sm font-semibold text-blue-400">{fmt(pkg.package_arv)}</p>
                    </div>
                    <div className="bg-slate-800/30 rounded-lg p-2.5">
                      <p className="text-[10px] text-slate-500">Savings vs Individual</p>
                      <p className={`text-sm font-semibold ${pkg.savings > 0 ? "text-emerald-400" : "text-slate-400"}`}>{fmt(pkg.savings)}</p>
                    </div>
                  </div>

                  {/* Campaign stats */}
                  {pkg.campaign_stats && (
                    <div className="grid grid-cols-4 gap-2">
                      <div className="text-center bg-slate-800/20 rounded-lg p-2">
                        <p className="text-lg font-bold text-white">{pkg.campaign_stats.total}</p>
                        <p className="text-[10px] text-slate-500">Total</p>
                      </div>
                      <div className="text-center bg-slate-800/20 rounded-lg p-2">
                        <p className="text-lg font-bold text-blue-400">{pkg.campaign_stats.sent}</p>
                        <p className="text-[10px] text-slate-500">Sent</p>
                      </div>
                      <div className="text-center bg-slate-800/20 rounded-lg p-2">
                        <p className="text-lg font-bold text-emerald-400">{pkg.campaign_stats.replied}</p>
                        <p className="text-[10px] text-slate-500">Replied</p>
                      </div>
                      <div className="text-center bg-slate-800/20 rounded-lg p-2">
                        <p className="text-lg font-bold text-red-400">{pkg.campaign_stats.passed}</p>
                        <p className="text-[10px] text-slate-500">Passed</p>
                      </div>
                    </div>
                  )}

                  {pkg.description && (
                    <p className="text-xs text-slate-400 bg-slate-800/20 rounded-lg p-2">{pkg.description}</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Create Package Modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={() => !creating && setShowCreate(false)} />
            <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-slate-700/50 bg-slate-900 shadow-2xl">
              <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/50">
                <h2 className="text-base font-semibold text-white">Create Package</h2>
                <button onClick={() => setShowCreate(false)} className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="px-5 py-4 space-y-4">
                {/* Name */}
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Package Name *</label>
                  <input
                    value={form.name}
                    onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors"
                    placeholder="e.g. San Antonio 3-Pack"
                  />
                </div>

                {/* Deals Multi-select */}
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Deals (select 2-5) *</label>
                  <div className="max-h-40 overflow-y-auto space-y-1 border border-slate-700 rounded-lg p-1.5 bg-slate-800/30">
                    {deals.filter(d => d.status !== "Dead" && d.status !== "Sold").map(d => (
                      <label key={d.id} className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-xs cursor-pointer transition-colors ${
                        form.deal_ids.includes(d.id) ? "bg-blue-500/10 text-blue-300" : "text-slate-400 hover:bg-slate-800/50"
                      }`}>
                        <input
                          type="checkbox"
                          checked={form.deal_ids.includes(d.id)}
                          onChange={e => {
                            if (e.target.checked) {
                              if (form.deal_ids.length >= 5) return;
                              setForm(prev => ({ ...prev, deal_ids: [...prev.deal_ids, d.id] }));
                            } else {
                              setForm(prev => ({ ...prev, deal_ids: prev.deal_ids.filter(id => id !== d.id) }));
                            }
                          }}
                          className="rounded border-slate-600"
                        />
                        <span className="truncate">{d.address}</span>
                        <span className="ml-auto shrink-0 text-slate-500">{d.asking_price ? `$${d.asking_price.toLocaleString()}` : ""}</span>
                      </label>
                    ))}
                  </div>
                  <p className="text-[10px] text-slate-600 mt-1">{form.deal_ids.length} of 5 selected</p>
                </div>

                {/* Price Fields */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-400 mb-1">Package Price ($) *</label>
                    <input type="number" value={form.package_price}
                      onChange={e => setForm(prev => ({ ...prev, package_price: e.target.value }))}
                      className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors" />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-400 mb-1">Floor Price ($) *</label>
                    <input type="number" value={form.floor_price}
                      onChange={e => setForm(prev => ({ ...prev, floor_price: e.target.value }))}
                      className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors" />
                  </div>
                </div>

                {/* Savings Preview */}
                {form.deal_ids.length >= 2 && pkgPrice > 0 && (
                  <div className="px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-300">
                    Savings vs individual: {fmt(savings)} {savings > 0 ? "✅" : "⚠️ Discount below sum of individual prices"}
                  </div>
                )}

                {/* Optional Fields */}
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Description (optional)</label>
                  <textarea value={form.description}
                    onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors min-h-[60px] resize-y" />
                </div>

                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-1">Expiry Date (optional)</label>
                  <input type="datetime-local" value={form.expiry_date}
                    onChange={e => setForm(prev => ({ ...prev, expiry_date: e.target.value }))}
                    className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors" />
                </div>

                {/* Actions */}
                <div className="flex justify-end gap-3 pt-2 border-t border-slate-700/50">
                  <button onClick={() => setShowCreate(false)}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors">
                    Cancel
                  </button>
                  <button
                    onClick={handleCreate}
                    disabled={creating || !form.name || !form.package_price || !form.floor_price || form.deal_ids.length < 2}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50">
                    {creating ? "Creating..." : "Create Package"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

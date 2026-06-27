"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Building2,
  Users,
  Send,
  DollarSign,
  TrendingUp,
  Target,
  Clock,
  CheckCircle2,
  AlertCircle,
  X,
  Zap,
  MapPin,
  Home,
  TreePine,
  BarChart3,
  Activity,
} from "lucide-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { apiFetch } from "@/lib/api";
import StatCard from "@/components/ui/StatCard";
import { usePolling } from "@/hooks/usePolling";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Deal {
  id: string;
  address: string;
  city: string | null;
  state: string | null;
  property_type: string;
  status: string;
  arv: number;
  asking_price: number;
  contract_price: number;
  spread: number | null;
  assigned_buyer_id: string | null;
  closed_at: string | null;
  closed_price: number | null;
  my_payout: number | null;
  jv_split_percentage: number | null;
  created_at: string;
}

interface Buyer {
  id: string;
  full_name: string;
  email: string;
  buyer_tier: string;
  status: string;
  engagement_score: number;
  created_at: string;
}

interface Campaign {
  id: string;
  deal_id: string;
  buyer_id: string;
  touch_number: number;
  status: string;
  sent_at: string | null;
  reply_received_at: string | null;
  reply_intent: string | null;
}

interface SendingStatus {
  sends_today: number;
  daily_cap: number;
  remaining: number;
  percent_used: number;
  cap_hit: boolean;
  warning_threshold_hit: boolean;
  resets_at: string;
}

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

function formatDate(iso: string | null) {
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getMonthYearKey(iso: string): { key: string; label: string } {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return {
    key: `${y}-${m}`,
    label: d.toLocaleDateString("en-US", { month: "short", year: "2-digit" }),
  };
}

const statusConfig: Record<string, { label: string; dot: string }> = {
  Available: { label: "Available", dot: "bg-emerald-400" },
  "Under Contract": { label: "Under Contract", dot: "bg-blue-400" },
  Sold: { label: "Sold", dot: "bg-purple-400" },
  Dead: { label: "Dead", dot: "bg-red-400" },
  "Campaign Launched": { label: "Campaign Launched", dot: "bg-amber-400" },
  Contract_Pending: { label: "Contract Pending", dot: "bg-sky-400" },
};

function StatusDot({ status }: { status: string }) {
  const cfg = statusConfig[status];
  return (
    <span className="flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${cfg?.dot || "bg-slate-500"}`} />
      <span className="text-xs text-slate-400">{cfg?.label || status}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Main Dashboard
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [buyers, setBuyers] = useState<Buyer[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [sendingStatus, setSendingStatus] = useState<SendingStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dealsData, buyersData, campaignsData, sendingData] = await Promise.all([
        apiFetch<Deal[]>("/api/deals"),
        apiFetch<Buyer[]>("/api/buyers"),
        apiFetch<Campaign[]>("/api/campaigns"),
        apiFetch<SendingStatus>("/api/sending/status").catch(() => null),
      ]);
      setDeals(dealsData);
      setBuyers(buyersData);
      setCampaigns(campaignsData);
      setSendingStatus(sendingData);
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

  // -----------------------------------------------------------------------
  // Computed stats
  // -----------------------------------------------------------------------

  const activeDeals = deals.filter((d) => d.status === "Available" || d.status === "Campaign Launched");
  const underContract = deals.filter((d) => d.status === "Under Contract");
  const soldDeals = deals.filter((d) => d.status === "Sold");
  const deadDeals = deals.filter((d) => d.status === "Dead");

  const activeBuyers = buyers.filter((b) => b.status === "Active");
  const sentCampaigns = campaigns.filter((c) => c.status === "Sent" || c.status === "Replied");
  const repliesReceived = campaigns.filter((c) => c.status === "Replied");
  const replyRate = sentCampaigns.length > 0
    ? Math.round((repliesReceived.length / sentCampaigns.length) * 100)
    : 0;

  const totalPotentialSpread = activeDeals.reduce((sum, d) => {
    const spread = d.spread || 0;
    const myShare = d.jv_split_percentage != null
      ? spread * (1 - d.jv_split_percentage / 100)
      : spread * 0.5;
    return sum + myShare;
  }, 0);
  const totalEarned = soldDeals.reduce((sum, d) => sum + (d.my_payout || 0), 0);

  // Monthly trend data
  const monthlyMap = new Map<string, { month: string; sortKey: string; deals: number; spread: number }>();
  [...activeDeals, ...underContract, ...soldDeals].forEach((d) => {
    const info = getMonthYearKey(d.created_at);
    const existing = monthlyMap.get(info.key) || { month: info.label, sortKey: info.key, deals: 0, spread: 0 };
    existing.deals += 1;
    existing.spread += d.spread || 0;
    monthlyMap.set(info.key, existing);
  });
  const monthlyData = Array.from(monthlyMap.values()).sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  // Recent deals
  const recentDeals = [...deals]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
    .slice(0, 5);

  // Top buyers by engagement
  const topBuyers = [...buyers]
    .sort((a, b) => b.engagement_score - a.engagement_score)
    .slice(0, 5);

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <div className="text-center space-y-4">
          <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-blue-500 to-purple-600 animate-pulse mx-auto" />
          <p className="text-sm text-slate-500 animate-pulse">Loading dashboard...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-white tracking-tight">Dashboard</h1>
              <p className="text-sm text-slate-500 mt-0.5">
                {deals.length} deals &middot; {buyers.length} buyers &middot; {campaigns.length} campaigns
              </p>
            </div>
            <button
              onClick={loadData}
              className="btn-secondary text-xs"
            >
              <BarChart3 className="w-3.5 h-3.5" />
              Refresh
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-8">
        {/* Error banner */}
        {error && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <span>{error}</span>
            <button onClick={() => setError(null)} className="ml-auto hover:text-red-300">
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* =============================================================== */}
        {/* Pipeline Overview */}
        {/* =============================================================== */}
        <section>
          <div className="flex items-center gap-2.5 pb-4 border-b border-slate-800/50">
            <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
              <Activity className="w-4 h-4" />
            </div>
            <h2 className="text-lg font-semibold text-white">Pipeline Overview</h2>
          </div>

          {/* Gmail send quota status bar */}
          {sendingStatus && sendingStatus.cap_hit && (
            <div className="mt-4 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-2.5 text-sm flex items-center gap-2">
              <span>🚫 Daily email cap reached ({sendingStatus.sends_today}/{sendingStatus.daily_cap}). Campaign sends paused. Resets at {new Date(sendingStatus.resets_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}. Reply emails still sending.</span>
            </div>
          )}
          {sendingStatus && !sendingStatus.cap_hit && sendingStatus.warning_threshold_hit && (
            <div className="mt-4 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-400 px-4 py-2.5 text-sm flex items-center gap-2">
              <span>⚡ {sendingStatus.sends_today}/{sendingStatus.daily_cap} emails sent today — {sendingStatus.remaining} remaining. Resets at {new Date(sendingStatus.resets_at).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })}.</span>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
            <StatCard
              title="Active Deals"
              value={activeDeals.length}
              subtitle={`${deals.filter(d => d.status === "Campaign Launched").length} in campaign`}
              icon={<Building2 className="w-4 h-4" />}
              color="blue"
              delay={0}
            />
            <StatCard
              title="Under Contract"
              value={underContract.length}
              subtitle={`${soldDeals.length} total sold`}
              icon={<Target className="w-4 h-4" />}
              color="amber"
              delay={50}
            />
            <StatCard
              title="Active Buyers"
              value={activeBuyers.length}
              subtitle={`${replyRate}% reply rate`}
              icon={<Users className="w-4 h-4" />}
              color="emerald"
              trend={{ value: `${repliesReceived.length} replies`, up: replyRate > 30 }}
              delay={100}
            />
            <StatCard
              title="Total Value"
              value={formatCurrency(totalEarned + totalPotentialSpread)}
              subtitle={`${formatCurrency(totalEarned)} your cut earned`}
              icon={<DollarSign className="w-4 h-4" />}
              color="purple"
              delay={150}
            />
          </div>
        </section>

        {/* =============================================================== */}
        {/* Charts Row */}
        {/* =============================================================== */}
        <section>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Monthly deals chart */}
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5 transition-all duration-300 hover:border-slate-700/50">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-slate-300">Monthly Deal Volume</h3>
                <TrendingUp className="w-4 h-4 text-emerald-400" />
              </div>
              {monthlyData.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-12">No data yet — add your first deal</p>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={monthlyData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="month" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{
                        background: "#0f172a",
                        border: "1px solid #334155",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                      labelStyle={{ color: "#94a3b8" }}
                    />
                    <Bar dataKey="deals" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Deals" />
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* Monthly spread trend */}
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5 transition-all duration-300 hover:border-slate-700/50">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-slate-300">Spread Trend</h3>
                <DollarSign className="w-4 h-4 text-emerald-400" />
              </div>
              {monthlyData.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-12">No data yet</p>
              ) : (
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={monthlyData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="month" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
                    <Tooltip
                      contentStyle={{
                        background: "#0f172a",
                        border: "1px solid #334155",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                      labelStyle={{ color: "#94a3b8" }}
                      formatter={(value: number) => [formatCurrency(value), "Spread"]}
                    />
                    <Line type="monotone" dataKey="spread" stroke="#8b5cf6" strokeWidth={2} dot={{ fill: "#8b5cf6", r: 3 }} activeDot={{ r: 5 }} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </section>

        {/* =============================================================== */}
        {/* Recent Deals + Top Buyers */}
        {/* =============================================================== */}
        <section>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Recent Deals */}
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 overflow-hidden transition-all duration-300 hover:border-slate-700/50">
              <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800/50">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-amber-400" />
                  <h3 className="text-sm font-semibold text-slate-300">Recent Deals</h3>
                </div>
                <span className="text-[10px] text-slate-600">{deals.length} total</span>
              </div>
              {recentDeals.length === 0 ? (
                <div className="flex flex-col items-center py-10 text-center">
                  <Building2 className="w-8 h-8 text-slate-700 mb-2" />
                  <p className="text-xs text-slate-500">No deals yet</p>
                </div>
              ) : (
                <div className="divide-y divide-slate-800/30">
                  {recentDeals.map((d, i) => (
                    <div
                      key={d.id}
                      className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-slate-800/30"
                      style={{ animation: `fadeIn 0.3s ease-out ${i * 50}ms forwards`, opacity: 0 }}
                    >
                      <div className={`w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold ${
                        d.property_type === "House"
                          ? "bg-blue-500/10 text-blue-400"
                          : "bg-emerald-500/10 text-emerald-400"
                      }`}>
                        {d.property_type === "House" ? "H" : "L"}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-white truncate">{d.address}</p>
                        <div className="flex items-center gap-2 mt-0.5">
                          <StatusDot status={d.status} />
                          {d.city && <span className="text-[10px] text-slate-600">{d.city}</span>}
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="text-sm font-medium text-slate-300 tabular-nums">
                          {formatCurrency(d.spread || d.asking_price)}
                        </p>
                        <p className="text-[10px] text-slate-600">{formatDate(d.created_at)}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Top Buyers */}
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 overflow-hidden transition-all duration-300 hover:border-slate-700/50">
              <div className="flex items-center justify-between px-5 py-4 border-b border-slate-800/50">
                <div className="flex items-center gap-2">
                  <Users className="w-4 h-4 text-blue-400" />
                  <h3 className="text-sm font-semibold text-slate-300">Top Buyers</h3>
                </div>
                <span className="text-[10px] text-slate-600">By engagement</span>
              </div>
              {topBuyers.length === 0 ? (
                <div className="flex flex-col items-center py-10 text-center">
                  <Users className="w-8 h-8 text-slate-700 mb-2" />
                  <p className="text-xs text-slate-500">No buyers yet</p>
                </div>
              ) : (
                <div className="divide-y divide-slate-800/30">
                  {topBuyers.map((b, i) => {
                    const initials = b.full_name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase();
                    const scoreColor = b.engagement_score >= 70 ? "text-emerald-400" : b.engagement_score >= 40 ? "text-amber-400" : "text-red-400";
                    return (
                      <div
                        key={b.id}
                        className="flex items-center gap-3 px-5 py-3 transition-colors hover:bg-slate-800/30"
                        style={{ animation: `fadeIn 0.3s ease-out ${i * 50}ms forwards`, opacity: 0 }}
                      >
                        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center text-xs font-bold text-blue-400">
                          {initials}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-white truncate">{b.full_name}</p>
                          <p className="text-[10px] text-slate-500 truncate">{b.email}</p>
                        </div>
                        <div className="text-right">
                          <p className={`text-sm font-bold tabular-nums ${scoreColor}`}>
                            {b.engagement_score.toFixed(0)}%
                          </p>
                          <span className="text-[10px] text-slate-600">{b.buyer_tier}</span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </section>

        {/* =============================================================== */}
        {/* Quick Stats */}
        {/* =============================================================== */}
        <section>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center transition-all duration-300 hover:border-slate-700/50 hover:bg-slate-800/50">
              <Send className="w-5 h-5 text-blue-400 mx-auto mb-2" />
              <p className="text-xl font-bold text-white">{sentCampaigns.length}</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Emails Sent</p>
            </div>
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center transition-all duration-300 hover:border-slate-700/50 hover:bg-slate-800/50">
              <CheckCircle2 className="w-5 h-5 text-emerald-400 mx-auto mb-2" />
              <p className="text-xl font-bold text-white">{repliesReceived.length}</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Replies</p>
            </div>
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center transition-all duration-300 hover:border-slate-700/50 hover:bg-slate-800/50">
              <Clock className="w-5 h-5 text-amber-400 mx-auto mb-2" />
              <p className="text-xl font-bold text-white">{replyRate}%</p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Reply Rate</p>
            </div>
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-4 text-center transition-all duration-300 hover:border-slate-700/50 hover:bg-slate-800/50">
              <MapPin className="w-5 h-5 text-purple-400 mx-auto mb-2" />
              <p className="text-xl font-bold text-white">
                {new Set(deals.filter(d => d.city).map(d => d.city)).size}
              </p>
              <p className="text-[10px] text-slate-500 uppercase tracking-wider mt-0.5">Markets</p>
            </div>
          </div>
        </section>

        {/* Bottom spacing */}
        <div className="h-6" />
      </main>
    </div>
  );
}

"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  Building2,
  TrendingUp,
  TrendingDown,
  Users,
  Mail,
  DollarSign,
  Target,
  AlertTriangle,
  Flag,
  PieChart as PieChartIcon,
  Activity,
  MapPin,
} from "lucide-react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Deal {
  id: string;
  address: string;
  city: string | null;
  state: string | null;
  county: string | null;
  property_type: string;
  status: string;
  arv: number;
  asking_price: number;
  contract_price: number;
  spread: number | null;
  assigned_buyer_id: string | null;
  jv_partner_id: string | null;
  closed_at: string | null;
  closed_price: number | null;
  created_at: string;
}

interface Buyer {
  id: string;
  full_name: string;
  email: string;
  buyer_tier: string;
  status: string;
  deals_closed: number;
  engagement_score: number;
  response_rate: number;
  deals_viewed: number;
  offers_accepted: number;
  created_at: string;
}

interface JVPartner {
  id: string;
  name: string;
  email: string;
  total_deals_submitted: number;
  total_deals_closed: number;
  avg_buyer_feedback_score: number;
  title_issue_rate: number;
  overprice_flag_count: number;
  total_revenue_generated: number;
  total_split_revenue: number;
  deals_linked: string[];
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

import { apiFetch } from "@/lib/api";

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

function formatPercent(val: number | null | undefined): string {
  if (val == null) return "0%";
  return `${(val * 100).toFixed(1)}%`;
}

function formatScore(val: number | null | undefined): string {
  if (val == null) return "0.0";
  return val.toFixed(1);
}

function getMonthYearKey(iso: string | null): { key: string; label: string } | null {
  if (!iso) return null;
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  return {
    key: `${y}-${m}`,
    label: d.toLocaleDateString("en-US", { month: "short", year: "2-digit" }),
  };
}

const CHART_COLORS = [
  "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b", "#ef4444",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1",
];

function StatCard({
  title,
  value,
  subtitle,
  icon,
  trend,
  color,
}: {
  title: string;
  value: string;
  subtitle?: string;
  icon: React.ReactNode;
  trend?: { value: string; up: boolean };
  color: string;
}) {
  return (
    <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{title}</span>
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center ${color}`}>
          {icon}
        </div>
      </div>
      <div>
        <p className="text-2xl font-bold text-white">{value}</p>
        {subtitle && <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {trend && (
        <div className="flex items-center gap-1 text-xs">
          {trend.up ? (
            <TrendingUp className="w-3 h-3 text-emerald-400" />
          ) : (
            <TrendingDown className="w-3 h-3 text-red-400" />
          )}
          <span className={trend.up ? "text-emerald-400" : "text-red-400"}>{trend.value}</span>
        </div>
      )}
    </div>
  );
}

function SectionHeader({ title, icon }: { title: string; icon: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 pb-4 border-b border-slate-800/50">
      <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-400">
        {icon}
      </div>
      <h2 className="text-lg font-semibold text-white">{title}</h2>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function AnalyticsPage() {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [buyers, setBuyers] = useState<Buyer[]>([]);
  const [jvPartners, setJvPartners] = useState<JVPartner[]>([]);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [dealsData, buyersData, jvData, campaignsData] = await Promise.all([
        apiFetch<Deal[]>("/api/deals"),
        apiFetch<Buyer[]>("/api/buyers"),
        apiFetch<JVPartner[]>("/api/jv-partners"),
        apiFetch<Campaign[]>("/api/campaigns"),
      ]);
      setDeals(dealsData);
      setBuyers(buyersData);
      setJvPartners(jvData);
      setCampaigns(campaignsData);
    } catch (err) {
      console.error("Analytics data load failed:", err)
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
  // Compute derived stats
  // -----------------------------------------------------------------------

  const activeDeals = deals.filter((d) => d.status === "Available");
  const underContract = deals.filter((d) => d.status === "Under Contract");
  const soldDeals = deals.filter((d) => d.status === "Sold");
  const deadDeals = deals.filter((d) => d.status === "Dead");
  const campaignLaunched = deals.filter((d) => d.status === "Campaign Launched");

  const now = new Date();
  const thisMonth = now.getMonth();
  const thisYear = now.getFullYear();
  const soldThisMonth = soldDeals.filter((d) => {
    if (!d.closed_at) return false;
    const c = new Date(d.closed_at);
    return c.getMonth() === thisMonth && c.getFullYear() === thisYear;
  });

  const totalSpread = deals.reduce((sum, d) => sum + (d.spread || 0), 0);
  const avgSpread = deals.length ? totalSpread / deals.length : 0;

  // Monthly spread data for line chart (sorted chronologically)
  const monthlySpreadMap = new Map<string, { month: string; sortKey: string; spread: number; count: number }>();
  [...activeDeals, ...underContract, ...soldDeals].forEach((d) => {
    const info = getMonthYearKey(d.created_at);
    if (!info) return;
    const existing = monthlySpreadMap.get(info.key) || { month: info.label, sortKey: info.key, spread: 0, count: 0 };
    existing.spread += d.spread || 0;
    existing.count += 1;
    monthlySpreadMap.set(info.key, existing);
  });
  const monthlySpreadData = Array.from(monthlySpreadMap.values())
    .sort((a, b) => a.sortKey.localeCompare(b.sortKey));

  // JV partner revenue data for pie chart
  const jvRevenueMap = new Map<string, { name: string; revenue: number }>();
  const jpNameMap = new Map(jvPartners.map((jp) => [jp.id, jp.name]));
  soldDeals.forEach((d) => {
    if (d.jv_partner_id) {
      const name = jpNameMap.get(d.jv_partner_id) || "Unknown";
      const existing = jvRevenueMap.get(d.jv_partner_id) || { name, revenue: 0 };
      existing.revenue += d.closed_price || 0;
      jvRevenueMap.set(d.jv_partner_id, existing);
    }
  });
  // Also add unallocated
  const unallocatedRevenue = soldDeals
    .filter((d) => !d.jv_partner_id)
    .reduce((sum, d) => sum + (d.closed_price || 0), 0);
  if (unallocatedRevenue > 0) {
    jvRevenueMap.set("unallocated", { name: "Direct", revenue: unallocatedRevenue });
  }
  const jvRevenueData = Array.from(jvRevenueMap.values()).sort((a, b) => b.revenue - a.revenue);

  // Top buyers by closed deals
  const topBuyers = [...buyers]
    .sort((a, b) => b.deals_closed - a.deals_closed)
    .slice(0, 10);

  // Campaign performance by touch number
  const touchStats = [1, 2, 3, 4, 5, 6].map((touch) => {
    const touchCampaigns = campaigns.filter((c) => c.touch_number === touch);
    const total = touchCampaigns.length;
    const sent = touchCampaigns.filter((c) => c.status === "Sent" || c.status === "Replied").length;
    const replied = touchCampaigns.filter((c) => c.status === "Replied").length;
    return {
      touch: `Touch #${touch}`,
      sent,
      replied,
      rate: sent > 0 ? Math.round((replied / sent) * 100) : 0,
    };
  });

  // Market heatmap by city
  const cityStatsMap = new Map<string, { city: string; deals: number; spread: number; buyers: Set<string> }>();
  deals.forEach((d) => {
    const city = d.city || "Unknown";
    const existing = cityStatsMap.get(city) || { city, deals: 0, spread: 0, buyers: new Set() };
    existing.deals += 1;
    existing.spread += d.spread || 0;
    if (d.assigned_buyer_id) existing.buyers.add(d.assigned_buyer_id);
    cityStatsMap.set(city, existing);
  });
  const cityStatsData = Array.from(cityStatsMap.values())
    .map((d) => ({
      city: d.city,
      deals: d.deals,
      avgSpread: d.deals > 0 ? Math.round(d.spread / d.deals) : 0,
      buyers: d.buyers.size,
    }))
    .sort((a, b) => b.deals - a.deals);

  // JV scorecards
  const jvScorecards = [...jvPartners].sort(
    (a, b) => b.total_deals_closed - a.total_deals_closed
  );

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex items-center justify-center">
        <svg className="animate-spin h-8 w-8 text-blue-500" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800/50 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <h1 className="text-2xl font-bold text-white tracking-tight">Analytics</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            {deals.length} deals &middot; {buyers.length} buyers &middot; {campaigns.length} campaigns
          </p>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6 space-y-8">
        {/* ================================================================= */}
        {/* 1. Pipeline Overview */}
        {/* ================================================================= */}
        <section>
          <SectionHeader title="Pipeline Overview" icon={<Activity className="w-4 h-4" />} />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-4">
            <StatCard
              title="Active Deals"
              value={activeDeals.length.toString()}
              subtitle={`${campaignLaunched.length} in campaign`}
              icon={<Building2 className="w-4 h-4 text-white" />}
              color="bg-blue-500/20"
            />
            <StatCard
              title="Under Contract"
              value={underContract.length.toString()}
              subtitle={`Avg spread ${formatCurrency(avgSpread)}`}
              icon={<Target className="w-4 h-4 text-white" />}
              color="bg-amber-500/20"
            />
            <StatCard
              title="Sold This Month"
              value={soldThisMonth.length.toString()}
              subtitle={`${soldDeals.length} total sold`}
              icon={<TrendingUp className="w-4 h-4 text-white" />}
              color="bg-emerald-500/20"
              trend={{
                value: `${soldDeals.length > 0 ? Math.round((soldThisMonth.length / soldDeals.length) * 100) : 0}% of all sales`,
                up: soldThisMonth.length > 0,
              }}
            />
            <StatCard
              title="Dead Deals"
              value={deadDeals.length.toString()}
              subtitle={`${deals.length > 0 ? Math.round((deadDeals.length / deals.length) * 100) : 0}% fall-through rate`}
              icon={<TrendingDown className="w-4 h-4 text-white" />}
              color="bg-red-500/20"
            />
          </div>
        </section>

        {/* ================================================================= */}
        {/* 2. Revenue */}
        {/* ================================================================= */}
        <section>
          <SectionHeader title="Revenue" icon={<DollarSign className="w-4 h-4" />} />
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
            {/* Monthly Spread Line Chart */}
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5">
              <h3 className="text-sm font-semibold text-slate-300 mb-4">Monthly Spread Trend</h3>
              {monthlySpreadData.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-12">No data yet</p>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={monthlySpreadData}>
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
                    <Line type="monotone" dataKey="spread" stroke="#3b82f6" strokeWidth={2} dot={{ fill: "#3b82f6", r: 4 }} activeDot={{ r: 6 }} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>

            {/* JV Partner Revenue Pie Chart */}
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5">
              <h3 className="text-sm font-semibold text-slate-300 mb-4">Revenue by Source</h3>
              {jvRevenueData.length === 0 ? (
                <p className="text-sm text-slate-500 text-center py-12">No closed deals yet</p>
              ) : (
                <ResponsiveContainer width="100%" height={260}>
                  <PieChart>
                    <Pie
                      data={jvRevenueData}
                      dataKey="revenue"
                      nameKey="name"
                      cx="50%"
                      cy="50%"
                      outerRadius={90}
                      innerRadius={50}
                      paddingAngle={3}
                    >
                      {jvRevenueData.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: "#0f172a",
                        border: "1px solid #334155",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                      formatter={(value: number) => [formatCurrency(value), "Revenue"]}
                    />
                    <Legend
                      wrapperStyle={{ fontSize: "11px", color: "#94a3b8" }}
                      formatter={(value: string) => <span style={{ color: "#94a3b8" }}>{value}</span>}
                    />
                  </PieChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* 3. Buyer Intelligence */}
        {/* ================================================================= */}
        <section>
          <SectionHeader title="Buyer Intelligence" icon={<Users className="w-4 h-4" />} />
          <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 overflow-hidden mt-4">
            {topBuyers.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-12">No buyers yet</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Buyer</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Closed</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Response Rate</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Engagement</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Deals Viewed</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Offers Accepted</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {topBuyers.map((b) => (
                      <tr key={b.id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-full bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center text-xs font-semibold text-blue-400">
                              {b.full_name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()}
                            </div>
                            <div>
                              <p className="font-medium text-white">{b.full_name}</p>
                              <p className="text-[10px] text-slate-500">{b.buyer_tier}</p>
                            </div>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right text-white font-medium tabular-nums">{b.deals_closed}</td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{formatPercent(b.response_rate)}</td>
                        <td className="px-4 py-3 text-right tabular-nums">
                          <span className={`${b.engagement_score >= 70 ? "text-emerald-400" : b.engagement_score >= 40 ? "text-amber-400" : "text-red-400"}`}>
                            {formatScore(b.engagement_score)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{b.deals_viewed}</td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{b.offers_accepted}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>

        {/* ================================================================= */}
        {/* 4. JV Intelligence */}
        {/* ================================================================= */}
        <section>
          <SectionHeader title="JV Intelligence" icon={<PieChartIcon className="w-4 h-4" />} />
          {jvScorecards.length === 0 ? (
            <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 py-12 text-center mt-4">
              <p className="text-sm text-slate-500">No JV partners yet</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
              {jvScorecards.map((jp) => (
                <div key={jp.id} className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5 space-y-4">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-cyan-500/20 to-blue-500/20 flex items-center justify-center text-xs font-semibold text-cyan-400">
                      {jp.name.split(" ").map((n) => n[0]).join("").slice(0, 2).toUpperCase()}
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-white">{jp.name}</p>
                      <p className="text-[10px] text-slate-500">{jp.email}</p>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-center">
                    <div className="bg-slate-800/50 rounded-lg p-2.5">
                      <p className="text-lg font-bold text-white">{jp.total_deals_submitted}</p>
                      <p className="text-[10px] text-slate-500">Submitted</p>
                    </div>
                    <div className="bg-slate-800/50 rounded-lg p-2.5">
                      <p className="text-lg font-bold text-white">{jp.total_deals_closed}</p>
                      <p className="text-[10px] text-slate-500">Closed</p>
                    </div>
                    <div className="bg-slate-800/50 rounded-lg p-2.5">
                      <p className="text-lg font-bold text-emerald-400">{jp.total_deals_submitted > 0 ? Math.round((jp.total_deals_closed / jp.total_deals_submitted) * 100) : 0}%</p>
                      <p className="text-[10px] text-slate-500">Close Rate</p>
                    </div>
                    <div className="bg-slate-800/50 rounded-lg p-2.5">
                      <p className="text-lg font-bold text-amber-400">{formatScore(jp.avg_buyer_feedback_score)}</p>
                      <p className="text-[10px] text-slate-500">Avg Feedback</p>
                    </div>
                  </div>
                  <div className="flex items-center justify-between text-xs text-slate-500 pt-2 border-t border-slate-800/30">
                    <span className="inline-flex items-center gap-1">
                      <AlertTriangle className="w-3 h-3 text-red-400" />
                      Title issues: {jp.title_issue_rate > 0 ? `${(jp.title_issue_rate * 100).toFixed(0)}%` : "0%"}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <Flag className="w-3 h-3 text-amber-400" />
                      Overprice: {jp.overprice_flag_count}
                    </span>
                    <span className="text-emerald-400 font-medium">{formatCurrency(jp.total_revenue_generated)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ================================================================= */}
        {/* 5. Campaign Performance */}
        {/* ================================================================= */}
        <section>
          <SectionHeader title="Campaign Performance" icon={<Mail className="w-4 h-4" />} />
          <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 p-5 mt-4">
            {touchStats.every((t) => t.sent === 0) ? (
              <p className="text-sm text-slate-500 text-center py-12">No campaigns sent yet</p>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={260}>
                  <BarChart data={touchStats}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="touch" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                    <YAxis tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                    <Tooltip
                      contentStyle={{
                        background: "#0f172a",
                        border: "1px solid #334155",
                        borderRadius: "8px",
                        fontSize: "12px",
                      }}
                      labelStyle={{ color: "#94a3b8" }}
                    />
                    <Legend wrapperStyle={{ fontSize: "11px", color: "#94a3b8" }} />
                    <Bar dataKey="sent" fill="#3b82f6" radius={[4, 4, 0, 0]} name="Sent" />
                    <Bar dataKey="replied" fill="#10b981" radius={[4, 4, 0, 0]} name="Replied" />
                  </BarChart>
                </ResponsiveContainer>
                <div className="grid grid-cols-6 gap-3 mt-4">
                  {touchStats.map((t) => (
                    <div key={t.touch} className="text-center bg-slate-800/30 rounded-lg p-2">
                      <p className="text-xs text-slate-500">{t.touch}</p>
                      <p className="text-sm font-bold text-white mt-1">{t.rate}%</p>
                      <p className="text-[9px] text-slate-600">Reply rate</p>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </section>

        {/* ================================================================= */}
        {/* 6. Market Heatmap */}
        {/* ================================================================= */}
        <section>
          <SectionHeader title="Market Heatmap" icon={<MapPin className="w-4 h-4" />} />
          <div className="rounded-xl border border-slate-800/50 bg-slate-900/50 overflow-hidden mt-4">
            {cityStatsData.length === 0 ? (
              <p className="text-sm text-slate-500 text-center py-12">No deal data yet</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">City</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Deals</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Avg Spread</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Unique Buyers</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {cityStatsData.map((c) => (
                      <tr key={c.city} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <MapPin className="w-3.5 h-3.5 text-slate-500" />
                            <span className="font-medium text-white">{c.city}</span>
                          </div>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className="inline-flex items-center justify-center min-w-[28px] h-7 rounded-md text-xs font-medium"
                            style={{
                              background: c.deals > 5 ? "rgba(59, 130, 246, 0.2)" : c.deals > 2 ? "rgba(59, 130, 246, 0.1)" : "rgba(59, 130, 246, 0.05)",
                              color: c.deals > 5 ? "#60a5fa" : c.deals > 2 ? "#93c5fd" : "#bfdbfe",
                            }}
                          >
                            {c.deals}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{formatCurrency(c.avgSpread)}</td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">{c.buyers}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>

        {/* Bottom spacing */}
        <div className="h-8" />
      </main>
    </div>
  );
}

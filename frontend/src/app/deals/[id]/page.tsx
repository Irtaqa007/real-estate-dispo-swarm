"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import {
  ArrowLeft, Building2, DollarSign, Wrench, TrendingUp,
  Users, CheckCircle2, XCircle, Clock, ChevronDown, ChevronUp,
  Plus, X, AlertCircle
} from "lucide-react";

interface Deal {
  id: string;
  address: string;
  city: string | null;
  state: string | null;
  zip: string | null;
  property_type: string;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  year_built: number | null;
  condition_description: string | null;
  arv: number;
  asking_price: number;
  floor_price: number;
  contract_price: number;
  repair_estimate: number | null;
  title_status: string | null;
  status: string;
  jv_partner_id: string | null;
  jv_split_percentage: number | null;
  spread: number | null;
  my_payout: number | null;
  jv_payout: number | null;
  pass_count: number | null;
  created_at: string;
}

interface Campaign {
  id: string;
  buyer_id: string;
  touch_number: number;
  status: string;
  conversation_stage: string | null;
  sent_at: string | null;
  scheduled_send_at: string | null;
  subject: string | null;
  body: string | null;
  reply_received_at: string | null;
  reply_body: string | null;
  reply_intent: string | null;
  ai_extracted_insights: string | null;
  buyer_legal_name: string | null;
  buyer_phone: string | null;
  buyer_title_company: string | null;
  agreed_price: number | null;
  pass_reason_raw: string | null;
}

interface Buyer {
  id: string;
  full_name: string;
  email: string;
  buyer_tier: string;
}

interface TitleCompany {
  id: string;
  company_name: string;
  contact_name: string | null;
  contact_email: string;
  contact_phone: string | null;
  file_number: string | null;
  status: string;
  notes: string | null;
}

const fmt = (n: number | null | undefined) =>
  n == null ? "—" : "$" + n.toLocaleString();

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

const stageColor: Record<string, string> = {
  pitching: "bg-slate-700 text-slate-300",
  engaging: "bg-blue-500/20 text-blue-300",
  qualifying: "bg-yellow-500/20 text-yellow-300",
  collecting_info: "bg-orange-500/20 text-orange-300",
  contract_ready: "bg-emerald-500/20 text-emerald-300",
  passed: "bg-red-500/20 text-red-400",
  dormant: "bg-slate-700 text-slate-500",
};

const statusColor: Record<string, string> = {
  Queued: "text-slate-400",
  Sent: "text-blue-400",
  Replied: "text-emerald-400",
  Passed: "text-red-400",
  Contract_Pending: "text-sky-400",
  Failed: "text-red-500",
  Paused: "text-slate-500",
};

const dealStatusConfig: Record<string, { dot: string; label: string }> = {
  Available: { dot: "bg-emerald-400", label: "Available" },
  "Campaign Launched": { dot: "bg-blue-400", label: "Campaign Launched" },
  "Under Contract": { dot: "bg-purple-400", label: "Under Contract" },
  Closed: { dot: "bg-slate-400", label: "Closed" },
};

export default function DealDetailPage() {
  const params = useParams();
  const dealId = params.id as string;

  const [deal, setDeal] = useState<Deal | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [buyers, setBuyers] = useState<Record<string, Buyer>>({});
  const [titleCompany, setTitleCompany] = useState<TitleCompany | null>(null);
  const [expandedCampaign, setExpandedCampaign] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showTCForm, setShowTCForm] = useState(false);
  const [tcSaving, setTcSaving] = useState(false);
  const [tcForm, setTcForm] = useState({
    company_name: "", contact_name: "", contact_email: "",
    contact_phone: "", file_number: "", status: "opened", notes: "",
  });

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        const [dealData, campaignsData] = await Promise.all([
          apiFetch<Deal>(`/api/deals/${dealId}`),
          apiFetch<Campaign[]>(`/api/deals/${dealId}/campaigns`),
        ]);
        setDeal(dealData);
        setCampaigns(campaignsData);

        const uniqueIds = [...new Set(campaignsData.map(c => c.buyer_id))];
        const buyerData: Record<string, Buyer> = {};
        await Promise.all(uniqueIds.map(async (bid) => {
          try {
            const b = await apiFetch<Buyer>(`/api/buyers/${bid}`);
            buyerData[bid] = b;
          } catch {}
        }));
        setBuyers(buyerData);

        try {
          const tcs = await apiFetch<TitleCompany[]>(`/api/title-companies/${dealId}`);
          if (tcs.length > 0) setTitleCompany(tcs[0]);
        } catch {}
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [dealId]);

  async function saveTitleCompany() {
    setTcSaving(true);
    try {
      const tc = await apiFetch<TitleCompany>("/api/title-companies", {
        method: "POST",
        body: JSON.stringify({ ...tcForm, deal_id: dealId }),
      });
      setTitleCompany(tc);
      setShowTCForm(false);
    } catch (e) {
      console.error(e);
    } finally {
      setTcSaving(false);
    }
  }

  if (loading) return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center">
      <div className="text-slate-400 text-sm">Loading deal...</div>
    </div>
  );

  if (error || !deal) return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center">
      <div className="text-red-400 text-sm">{error || "Deal not found"}</div>
    </div>
  );

  const buyerProfit = ((deal.arv || 0) - (deal.asking_price || 0) - (deal.repair_estimate || 0));
  const contractCampaigns = campaigns.filter(c => c.status === "Contract_Pending");
  const statusConf = dealStatusConfig[deal.status] || { dot: "bg-slate-400", label: deal.status };

  // Group campaigns by buyer
  const byBuyer: Record<string, Campaign[]> = {};
  campaigns.forEach(c => {
    if (!byBuyer[c.buyer_id]) byBuyer[c.buyer_id] = [];
    byBuyer[c.buyer_id].push(c);
  });

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Header */}
      <div className="border-b border-slate-800 bg-slate-900/60 backdrop-blur px-6 py-4">
        <div className="max-w-5xl mx-auto">
          <Link href="/deals" className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 transition-colors mb-3">
            <ArrowLeft className="w-4 h-4" />
            All Deals
          </Link>
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-semibold text-white">{deal.address}</h1>
              <p className="text-sm text-slate-400 mt-0.5">
                {deal.city}, {deal.state} {deal.zip} · {deal.property_type} · {deal.beds}bd/{deal.baths}ba
                {deal.sqft && ` · ${deal.sqft.toLocaleString()} sqft`}
                {deal.year_built && ` · Built ${deal.year_built}`}
              </p>
            </div>
            <span className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-slate-800 border border-slate-700 text-xs font-medium text-slate-300">
              <span className={`w-2 h-2 rounded-full ${statusConf.dot}`} />
              {statusConf.label}
            </span>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-6 space-y-5">

        {/* Contract Ready Alert */}
        {contractCampaigns.length > 0 && (
          <div className="bg-sky-500/10 border border-sky-500/30 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle2 className="w-4 h-4 text-sky-400" />
              <span className="text-sm font-semibold text-sky-300">Contract Ready</span>
            </div>
            {contractCampaigns.map(c => {
              const b = buyers[c.buyer_id];
              return (
                <div key={c.id} className="text-sm text-sky-200/80 space-y-1">
                  <p><span className="font-medium text-sky-200">{b?.full_name}</span> — {b?.email}</p>
                  {c.buyer_legal_name && <p>Legal name: {c.buyer_legal_name}</p>}
                  {c.buyer_phone && <p>Phone: {c.buyer_phone}</p>}
                  {c.buyer_title_company && <p>Title: {c.buyer_title_company}</p>}
                  {c.agreed_price && <p>Agreed: {fmt(c.agreed_price)}</p>}
                </div>
              );
            })}
          </div>
        )}

        {/* Numbers Grid */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: "Asking Price", value: fmt(deal.asking_price), icon: DollarSign, color: "text-slate-300" },
            { label: "ARV", value: fmt(deal.arv), icon: TrendingUp, color: "text-emerald-400" },
            { label: "Rehab Est.", value: deal.repair_estimate ? fmt(deal.repair_estimate) : "Not set", icon: Wrench, color: "text-amber-400" },
            { label: "Buyer Profit", value: fmt(buyerProfit), icon: TrendingUp, color: "text-emerald-400", highlight: true },
          ].map(({ label, value, icon: Icon, color, highlight }) => (
            <div key={label} className={`bg-slate-900 border rounded-xl p-4 ${highlight ? "border-emerald-500/30" : "border-slate-800"}`}>
              <div className="flex items-center gap-2 mb-2">
                <Icon className={`w-3.5 h-3.5 ${color}`} />
                <span className="text-xs text-slate-500">{label}</span>
              </div>
              <p className={`text-lg font-semibold ${highlight ? "text-emerald-400" : "text-white"}`}>{value}</p>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: "Contract Price", value: fmt(deal.contract_price) },
            { label: "Assignment Fee", value: fmt(deal.asking_price - deal.contract_price) },
            { label: "My Payout", value: fmt(deal.my_payout) },
            { label: "JV Split", value: deal.jv_split_percentage ? `${deal.jv_split_percentage}%` : "—" },
          ].map(({ label, value }) => (
            <div key={label} className="bg-slate-900 border border-slate-800 rounded-xl p-4">
              <p className="text-xs text-slate-500 mb-1">{label}</p>
              <p className="text-base font-medium text-slate-200">{value}</p>
            </div>
          ))}
        </div>

        {/* Condition */}
        {deal.condition_description && (
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
            <p className="text-xs text-slate-500 mb-1.5">Condition</p>
            <p className="text-sm text-slate-300">{deal.condition_description}</p>
          </div>
        )}

        {/* Campaign Stats */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: "Total Buyers", value: Object.keys(byBuyer).length },
            { label: "Active", value: campaigns.filter(c => ["Queued","Sent","Replied"].includes(c.status)).length },
            { label: "Replied", value: campaigns.filter(c => c.reply_body).length },
            { label: "Passed", value: campaigns.filter(c => c.status === "Passed").length },
          ].map(({ label, value }) => (
            <div key={label} className="bg-slate-900 border border-slate-800 rounded-xl p-4 text-center">
              <p className="text-2xl font-bold text-white">{value}</p>
              <p className="text-xs text-slate-500 mt-1">{label}</p>
            </div>
          ))}
        </div>

        {/* Title Company */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-slate-200">Title Company</h2>
            {!titleCompany && !showTCForm && (
              <button onClick={() => setShowTCForm(true)}
                className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors">
                <Plus className="w-3.5 h-3.5" /> Add
              </button>
            )}
          </div>
          {titleCompany ? (
            <div className="space-y-1">
              <p className="text-sm font-medium text-white">{titleCompany.company_name}</p>
              {titleCompany.contact_name && <p className="text-sm text-slate-400">{titleCompany.contact_name} · {titleCompany.contact_email}</p>}
              {titleCompany.contact_phone && <p className="text-sm text-slate-400">{titleCompany.contact_phone}</p>}
              {titleCompany.file_number && <p className="text-xs text-slate-500">File #{titleCompany.file_number}</p>}
              <span className="inline-block mt-1 px-2 py-0.5 rounded-full bg-slate-800 text-xs text-slate-400">{titleCompany.status}</span>
            </div>
          ) : showTCForm ? (
            <div className="space-y-3">
              {[
                { key: "company_name", label: "Company Name *" },
                { key: "contact_name", label: "Contact Name" },
                { key: "contact_email", label: "Contact Email *" },
                { key: "contact_phone", label: "Phone" },
                { key: "file_number", label: "File Number" },
              ].map(({ key, label }) => (
                <div key={key}>
                  <label className="block text-xs text-slate-500 mb-1">{label}</label>
                  <input
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500 transition-colors"
                    value={(tcForm as any)[key]}
                    onChange={e => setTcForm(prev => ({ ...prev, [key]: e.target.value }))}
                  />
                </div>
              ))}
              <div className="flex gap-2 pt-1">
                <button onClick={saveTitleCompany} disabled={tcSaving}
                  className="px-4 py-1.5 bg-blue-600 hover:bg-blue-500 text-white text-xs rounded-lg transition-colors disabled:opacity-50">
                  {tcSaving ? "Saving..." : "Save"}
                </button>
                <button onClick={() => setShowTCForm(false)}
                  className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs rounded-lg transition-colors">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-slate-500">No title company added yet.</p>
          )}
        </div>

        {/* Campaign History */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-800">
            <h2 className="text-sm font-medium text-slate-200">Campaign History</h2>
          </div>

          {campaigns.length === 0 ? (
            <p className="text-sm text-slate-500 p-4">No campaigns yet.</p>
          ) : (
            <div className="divide-y divide-slate-800">
              {campaigns.map(c => {
                const buyer = buyers[c.buyer_id];
                const isExpanded = expandedCampaign === c.id;
                const stage = c.conversation_stage || "pitching";

                return (
                  <div key={c.id}>
                    <button
                      onClick={() => setExpandedCampaign(isExpanded ? null : c.id)}
                      className="w-full text-left px-4 py-3 hover:bg-slate-800/50 transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3 min-w-0">
                          <span className="text-xs text-slate-500 w-14 flex-shrink-0">Touch {c.touch_number}</span>
                          <span className={`text-xs font-medium ${statusColor[c.status] || "text-slate-400"}`}>{c.status}</span>
                          <span className="text-sm font-medium text-slate-200 truncate">{buyer?.full_name || "..."}</span>
                          <span className={`px-2 py-0.5 rounded-full text-xs ${stageColor[stage] || "bg-slate-700 text-slate-400"}`}>{stage}</span>
                        </div>
                        <div className="flex items-center gap-3 flex-shrink-0 ml-3">
                          {c.reply_received_at && (
                            <span className="text-xs text-emerald-400">Replied {fmtDate(c.reply_received_at)}</span>
                          )}
                          <span className="text-xs text-slate-500">{fmtDate(c.sent_at || c.scheduled_send_at)}</span>
                          {isExpanded ? <ChevronUp className="w-3.5 h-3.5 text-slate-500" /> : <ChevronDown className="w-3.5 h-3.5 text-slate-500" />}
                        </div>
                      </div>
                      {c.subject && <p className="text-xs text-slate-500 mt-1 ml-14">{c.subject}</p>}
                    </button>

                    {isExpanded && (
                      <div className="px-4 pb-4 pt-2 space-y-3 bg-slate-950/50">
                        {c.body && (
                          <div>
                            <p className="text-xs font-medium text-slate-500 mb-1.5">Email sent</p>
                            <div className="bg-slate-900 border border-slate-800 rounded-lg p-3 text-sm text-slate-300 whitespace-pre-wrap max-h-48 overflow-y-auto">
                              {c.body}
                            </div>
                          </div>
                        )}
                        {c.reply_body && (
                          <div>
                            <p className="text-xs font-medium text-slate-500 mb-1.5">Buyer reply</p>
                            <div className="bg-blue-500/5 border border-blue-500/20 rounded-lg p-3 text-sm text-slate-300 whitespace-pre-wrap max-h-48 overflow-y-auto">
                              {c.reply_body.split(/\r?\nOn .{10,100}wrote:/)[0].trim()}
                            </div>
                          </div>
                        )}
                        {c.ai_extracted_insights && (
                          <div>
                            <p className="text-xs font-medium text-slate-500 mb-1.5">AI insight</p>
                            <p className="text-xs text-amber-300/80 bg-amber-500/5 border border-amber-500/20 rounded-lg p-2">{c.ai_extracted_insights}</p>
                          </div>
                        )}
                        {(c.buyer_legal_name || c.buyer_phone || c.buyer_title_company) && (
                          <div>
                            <p className="text-xs font-medium text-slate-500 mb-1.5">Contract info collected</p>
                            <div className="text-xs text-slate-400 space-y-0.5 bg-emerald-500/5 border border-emerald-500/20 rounded-lg p-2">
                              {c.buyer_legal_name && <p>Legal name: <span className="text-emerald-300">{c.buyer_legal_name}</span></p>}
                              {c.buyer_phone && <p>Phone: <span className="text-emerald-300">{c.buyer_phone}</span></p>}
                              {c.buyer_title_company && <p>Title: <span className="text-emerald-300">{c.buyer_title_company}</span></p>}
                              {c.agreed_price && <p>Agreed price: <span className="text-emerald-300">{fmt(c.agreed_price)}</span></p>}
                            </div>
                          </div>
                        )}
                        {c.pass_reason_raw && (
                          <div>
                            <p className="text-xs font-medium text-red-400 mb-1.5">Pass reason</p>
                            <p className="text-xs text-slate-400 bg-red-500/5 border border-red-500/20 rounded-lg p-2">{c.pass_reason_raw}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

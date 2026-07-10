"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { apiFetch } from "@/lib/api";
import {
  ArrowLeft, Building2, DollarSign, Wrench, TrendingUp,
  Users, CheckCircle2, XCircle, Clock, ChevronDown, ChevronUp,
  Plus, X, AlertCircle, MessageSquare, Send, CheckCircle,
  PauseCircle, Play, Calendar, Home, MapPin
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
  expiry_date: string | null;
  created_at: string;
}

interface DealComp {
  id: string;
  deal_id: string;
  address: string;
  sold_price: number;
  sold_date: string;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  distance_miles: number | null;
  notes: string | null;
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
  Paused: { dot: "bg-amber-400", label: "Paused" },
  Expired: { dot: "bg-red-400", label: "Expired" },
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
  // Manual reply modal state
  const [replyTarget, setReplyTarget] = useState<Campaign | null>(null);
  const [replyMessage, setReplyMessage] = useState("");
  const [replySending, setReplySending] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);
  const [replySuccess, setReplySuccess] = useState<string | null>(null);
  // Comps state
  const [comps, setComps] = useState<DealComp[]>([]);
  const [newComp, setNewComp] = useState({
    address: "", sold_price: "", sold_date: "",
    beds: "", baths: "", sqft: "", distance_miles: "", notes: ""
  });
  const [compAdding, setCompAdding] = useState(false);
  const [showCompForm, setShowCompForm] = useState(false);

  // Pause/Resume state
  const [pausing, setPausing] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [pauseReason, setPauseReason] = useState("");
  // Expiry date state
  const [expiryDate, setExpiryDate] = useState<string>("");
  const [expirySaving, setExpirySaving] = useState(false);
  const [showExpiryInput, setShowExpiryInput] = useState(false);

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

        // Load comps
        try {
          const compsData = await apiFetch<DealComp[]>(`/api/deals/${dealId}/comps`);
          setComps(compsData);
        } catch {}
      } catch (e) {
        setError(String(e));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [dealId]);

  async function handlePause() {
    setPausing(true);
    try {
      await apiFetch(`/api/campaigns/${dealId}/pause`, {
        method: "POST",
        body: JSON.stringify({ reason: pauseReason || "manual_pause" }),
      });
      // Refresh deal data
      const updated = await apiFetch<Deal>(`/api/deals/${dealId}`);
      setDeal(updated);
      setPauseReason("");
    } catch (e) {
      console.error(e);
    } finally {
      setPausing(false);
    }
  }

  async function handleSaveExpiryDate() {
    setExpirySaving(true);
    try {
      const payload: Record<string, unknown> = {};
      if (expiryDate) {
        payload.expiry_date = new Date(expiryDate).toISOString();
      } else {
        payload.expiry_date = null;
      }
      await apiFetch(`/api/deals/${dealId}`, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      const updated = await apiFetch<Deal>(`/api/deals/${dealId}`);
      setDeal(updated);
      setShowExpiryInput(false);
    } catch (e) {
      console.error(e);
    } finally {
      setExpirySaving(false);
    }
  }

  async function handleResume() {
    setResuming(true);
    try {
      await apiFetch(`/api/campaigns/${dealId}/resume`, {
        method: "POST",
      });
      const updated = await apiFetch<Deal>(`/api/deals/${dealId}`);
      setDeal(updated);
    } catch (e) {
      console.error(e);
    } finally {
      setResuming(false);
    }
  }

  async function sendManualReply() {
    if (!replyTarget || !replyMessage.trim()) return;
    setReplySending(true);
    setReplyError(null);
    setReplySuccess(null);
    try {
      await apiFetch(`/api/campaigns/${replyTarget.id}/manual-reply`, {
        method: "POST",
        body: JSON.stringify({ message: replyMessage.trim() }),
      });
      setReplySuccess(`Reply sent to ${buyers[replyTarget.buyer_id]?.full_name || replyTarget.buyer_id}`);
      // Update the local campaign state to reflect the manual reply
      setCampaigns(prev =>
        prev.map(c =>
          c.id === replyTarget.id
            ? { ...c, reply_body: `MANUAL: ${replyMessage.trim()}`, conversation_stage: c.conversation_stage === "pitching" ? "replied" : c.conversation_stage }
            : c
        )
      );
      setReplyMessage("");
      setTimeout(() => {
        setReplyTarget(null);
        setReplySuccess(null);
      }, 2000);
    } catch (err: any) {
      setReplyError(err.message);
    } finally {
      setReplySending(false);
    }
  }

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
            {/* Pause/Resume buttons */}
            {(deal.status === "Campaign Launched" || deal.status === "Available") && (
              <div className="flex items-center gap-2 mt-2">
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    placeholder="Reason (optional)"
                    value={pauseReason}
                    onChange={(e) => setPauseReason(e.target.value)}
                    className="w-36 px-2 py-1 rounded-md border border-slate-700 bg-slate-800/50 text-xs text-slate-300 placeholder-slate-600 focus:outline-none focus:border-amber-500/50 transition-colors"
                  />
                  <button
                    onClick={handlePause}
                    disabled={pausing}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 transition-colors disabled:opacity-50"
                  >
                    <PauseCircle className="w-3.5 h-3.5" />
                    {pausing ? "Pausing..." : "Pause Campaign"}
                  </button>
                </div>
              </div>
            )}
            {deal.status === "Paused" && (
              <div className="flex items-center gap-2 mt-2">
                <button
                  onClick={handleResume}
                  disabled={resuming}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-emerald-300 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 transition-colors disabled:opacity-50"
                >
                  <Play className="w-3.5 h-3.5" />
                  {resuming ? "Resuming..." : "Resume Campaign"}
                </button>
              </div>
            )}
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

        {/* Expiry Date */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs text-slate-500 flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5" />
              Expiry Date
            </p>
            <button
              onClick={() => {
                setExpiryDate(deal.expiry_date ? new Date(deal.expiry_date).toISOString().slice(0, 16) : "");
                setShowExpiryInput(!showExpiryInput);
              }}
              className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
            >
              {showExpiryInput ? "Cancel" : (deal.expiry_date ? "Edit" : "Set Date")}
            </button>
          </div>
          {showExpiryInput ? (
            <div className="flex items-center gap-2">
              <input
                type="datetime-local"
                value={expiryDate}
                onChange={(e) => setExpiryDate(e.target.value)}
                className="flex-1 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors"
              />
              <button
                onClick={handleSaveExpiryDate}
                disabled={expirySaving}
                className="px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50"
              >
                {expirySaving ? "Saving..." : "Save"}
              </button>
            </div>
          ) : (
            <p className={`text-sm ${deal.expiry_date ? "text-slate-300" : "text-slate-500 italic"}`}>
              {deal.expiry_date ? new Date(deal.expiry_date).toLocaleDateString("en-US", {
                month: "short", day: "numeric", year: "numeric",
                hour: "2-digit", minute: "2-digit",
              }) : "No expiry date set"}
            </p>
          )}
        </div>

        {/* Expiry Warning Banner */}
        {deal.expiry_date && (() => {
          const now = new Date();
          const expiry = new Date(deal.expiry_date);
          const daysUntil = Math.ceil((expiry.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
          if (daysUntil <= 3 && daysUntil >= 0) {
            return (
              <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>
                  This deal expires in <strong>{daysUntil === 0 ? "less than a day" : `${daysUntil} days`}</strong>
                  {daysUntil <= 0 ? " — campaigns will auto-stop." : " — review before expiry."}
                </span>
              </div>
            );
          }
          if (daysUntil < 0) {
            return (
              <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
                <XCircle className="w-4 h-4 shrink-0" />
                <span>
                  This deal expired <strong>{Math.abs(daysUntil)} days ago</strong>. Campaigns will be auto-stopped.
                </span>
              </div>
            );
          }
          return null;
        })()}

        {/* Comparable Sales */}
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-slate-200 flex items-center gap-2">
              <MapPin className="w-3.5 h-3.5 text-slate-400" />
              Comparable Sales ({comps.length}/5)
            </h2>
            {comps.length < 5 && !showCompForm && (
              <button onClick={() => setShowCompForm(true)}
                className="flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors">
                <Plus className="w-3.5 h-3.5" /> Add Comp
              </button>
            )}
          </div>

          {/* Add Comp Form */}
          {showCompForm && (
            <div className="mb-4 p-3 rounded-lg bg-slate-800/50 border border-slate-700 space-y-2">
              <div className="grid grid-cols-2 gap-2">
                {[
                  { key: "address", label: "Address *", type: "text", colSpan: 2 },
                  { key: "sold_price", label: "Sold Price ($) *", type: "number" },
                  { key: "sold_date", label: "Sold Date *", type: "date" },
                  { key: "beds", label: "Beds", type: "number" },
                  { key: "baths", label: "Baths", type: "number" },
                  { key: "sqft", label: "Sqft", type: "number" },
                  { key: "distance_miles", label: "Distance (mi)", type: "number" },
                  { key: "notes", label: "Notes", type: "text", colSpan: 2 },
                ].map(({ key, label, type, colSpan }) => (
                  <div key={key} className={colSpan === 2 ? "col-span-2" : ""}>
                    <label className="block text-xs text-slate-500 mb-0.5">{label}</label>
                    <input
                      type={type}
                      value={(newComp as any)[key]}
                      onChange={e => setNewComp(prev => ({ ...prev, [key]: e.target.value }))}
                      className="w-full px-2 py-1.5 rounded-md border border-slate-700 bg-slate-800/50 text-xs text-slate-200 focus:outline-none focus:border-blue-500/50 transition-colors"
                    />
                  </div>
                ))}
              </div>
              <div className="flex gap-2 pt-1">
                <button
                  onClick={async () => {
                    if (!newComp.address || !newComp.sold_price || !newComp.sold_date) return;
                    setCompAdding(true);
                    try {
                      const payload: Record<string, unknown> = {
                        address: newComp.address,
                        sold_price: parseFloat(newComp.sold_price),
                        sold_date: newComp.sold_date,
                      };
                      if (newComp.beds) payload.beds = parseInt(newComp.beds);
                      if (newComp.baths) payload.baths = parseFloat(newComp.baths);
                      if (newComp.sqft) payload.sqft = parseInt(newComp.sqft);
                      if (newComp.distance_miles) payload.distance_miles = parseFloat(newComp.distance_miles);
                      if (newComp.notes) payload.notes = newComp.notes;
                      const added = await apiFetch<DealComp>(`/api/deals/${dealId}/comps`, {
                        method: "POST",
                        body: JSON.stringify(payload),
                      });
                      setComps(prev => [...prev, added]);
                      setNewComp({ address: "", sold_price: "", sold_date: "", beds: "", baths: "", sqft: "", distance_miles: "", notes: "" });
                      setShowCompForm(false);
                    } catch (e: any) {
                      console.error(e);
                    } finally {
                      setCompAdding(false);
                    }
                  }}
                  disabled={compAdding || !newComp.address || !newComp.sold_price || !newComp.sold_date}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50"
                >
                  {compAdding ? "Adding..." : "Add"}
                </button>
                <button onClick={() => setShowCompForm(false)}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 transition-colors">
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Comp Cards */}
          {comps.length === 0 ? (
            <p className="text-sm text-slate-500 italic">No comparable sales added yet. Add comps to help the AI reference real market data in emails.</p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              {comps.map(c => (
                <div key={c.id} className="p-3 rounded-lg bg-slate-800/30 border border-slate-700/50 relative group">
                  <button
                    onClick={async () => {
                      try {
                        await apiFetch(`/api/deals/${dealId}/comps/${c.id}`, { method: "DELETE" });
                        setComps(prev => prev.filter(x => x.id !== c.id));
                      } catch (e) { console.error(e); }
                    }}
                    className="absolute top-2 right-2 p-1 rounded-md text-slate-600 hover:text-red-400 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 transition-all"
                    title="Delete comp"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                  <p className="text-sm font-medium text-slate-200 pr-6">{c.address}</p>
                  <div className="flex items-center gap-3 mt-1.5 text-xs text-slate-400">
                    <span className="text-emerald-400 font-semibold">${c.sold_price.toLocaleString()}</span>
                    <span>{new Date(c.sold_date).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}</span>
                    {c.beds && <span>{c.beds}bd</span>}
                    {c.baths && <span>{c.baths}ba</span>}
                    {c.sqft && <span>{c.sqft.toLocaleString()}sqft</span>}
                    {c.distance_miles && <span>{c.distance_miles}mi</span>}
                  </div>
                  {c.notes && <p className="text-xs text-slate-500 mt-1">{c.notes}</p>}
                </div>
              ))}
            </div>
          )}
        </div>

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
                          {(c.status === "Sent" || c.status === "Replied") && (
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                setReplyTarget(c);
                                setReplyMessage("");
                                setReplyError(null);
                                setReplySuccess(null);
                              }}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium text-blue-400 hover:text-blue-300 bg-blue-500/10 hover:bg-blue-500/20 transition-colors"
                              title="Send manual reply"
                            >
                              <MessageSquare className="w-3 h-3" />
                              Reply
                            </button>
                          )}
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

        {/* Manual Reply Modal */}
        {replyTarget && (
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={() => !replySending && setReplyTarget(null)} />
            <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-xl border border-slate-700/50 bg-slate-900 shadow-2xl">
              <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700/50">
                <h2 className="text-base font-semibold text-white">
                  Manual Reply
                </h2>
                <button
                  onClick={() => setReplyTarget(null)}
                  disabled={replySending}
                  className="p-1 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors disabled:opacity-50"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="px-5 py-4 space-y-4">
                {/* Recipient info */}
                <div className="flex items-center gap-2 text-sm text-slate-400">
                  <MessageSquare className="w-4 h-4 text-blue-400" />
                  <span>
                    Replying to{" "}
                    <span className="font-medium text-white">
                      {buyers[replyTarget.buyer_id]?.full_name || "Buyer"}
                    </span>
                    {replyTarget.subject && (
                      <span className="text-slate-500"> — {replyTarget.subject}</span>
                    )}
                  </span>
                </div>

                {/* Textarea */}
                <div>
                  <label className="block text-sm font-medium text-slate-400 mb-1.5">
                    Your Message
                  </label>
                  <textarea
                    className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors min-h-[120px] resize-y"
                    placeholder="Type your reply here..."
                    value={replyMessage}
                    onChange={(e) => setReplyMessage(e.target.value)}
                    disabled={replySending}
                    autoFocus
                  />
                  <p className="text-[10px] text-slate-600 mt-1">
                    Will be signed with your operator name
                  </p>
                </div>

                {/* Error */}
                {replyError && (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
                    <AlertCircle className="w-4 h-4 shrink-0" />
                    <span>{replyError}</span>
                  </div>
                )}

                {/* Success */}
                {replySuccess && (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs">
                    <CheckCircle className="w-4 h-4 shrink-0" />
                    <span>{replySuccess}</span>
                  </div>
                )}

                {/* Actions */}
                <div className="flex justify-end gap-3 pt-2 border-t border-slate-700/50">
                  <button
                    onClick={() => setReplyTarget(null)}
                    disabled={replySending}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={sendManualReply}
                    disabled={replySending || !replyMessage.trim()}
                    className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50 flex items-center gap-2"
                  >
                    {replySending ? (
                      <>
                        <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        Sending...
                      </>
                    ) : (
                      <>
                        <Send className="w-4 h-4" />
                        Send Reply
                      </>
                    )}
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

"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

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
  condition_description: string;
  arv: number;
  asking_price: number;
  floor_price: number;
  contract_price: number;
  repair_estimate: number | null;
  title_status: string;
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
  pass_reason_category: string | null;
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

const stageColors: Record<string, string> = {
  pitching: "bg-gray-100 text-gray-600",
  engaging: "bg-blue-100 text-blue-700",
  qualifying: "bg-yellow-100 text-yellow-700",
  collecting_info: "bg-orange-100 text-orange-700",
  contract_ready: "bg-green-100 text-green-700",
  passed: "bg-red-100 text-red-600",
  dormant: "bg-gray-100 text-gray-500",
};

const statusColors: Record<string, string> = {
  Queued: "text-gray-400",
  Sent: "text-blue-500",
  Replied: "text-green-500",
  Passed: "text-red-400",
  Contract_Pending: "text-sky-500",
  Failed: "text-red-600",
  Paused: "text-gray-400",
};

function fmt(n: number | null | undefined, prefix = "$") {
  if (n == null) return "—";
  return prefix + n.toLocaleString();
}

function fmtDate(s: string | null) {
  if (!s) return "—";
  return new Date(s).toLocaleDateString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function DealDetailPage() {
  const params = useParams();
  const router = useRouter();
  const dealId = params.id as string;

  const [deal, setDeal] = useState<Deal | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [buyers, setBuyers] = useState<Record<string, Buyer>>({});
  const [titleCompany, setTitleCompany] = useState<TitleCompany | null>(null);
  const [expandedCampaign, setExpandedCampaign] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Title company form
  const [showTCForm, setShowTCForm] = useState(false);
  const [tcForm, setTcForm] = useState({
    company_name: "", contact_name: "", contact_email: "",
    contact_phone: "", file_number: "", status: "opened", notes: "",
  });
  const [tcSaving, setTcSaving] = useState(false);

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

        // Load buyer names
        const uniqueBuyerIds = [...new Set(campaignsData.map(c => c.buyer_id))];
        const buyerData: Record<string, Buyer> = {};
        await Promise.all(
          uniqueBuyerIds.map(async (bid) => {
            try {
              const b = await apiFetch<Buyer>(`/api/buyers/${bid}`);
              buyerData[bid] = b;
            } catch {}
          })
        );
        setBuyers(buyerData);

        // Try load title company
        try {
          const tc = await apiFetch<TitleCompany[]>(`/api/title-companies/${dealId}`);
          if (tc.length > 0) setTitleCompany(tc[0]);
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
      const r = await fetch(`${API}/api/title-companies`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...tcForm, deal_id: dealId }),
      });
      if (r.ok) {
        const tc = await r.json();
        setTitleCompany(tc);
        setShowTCForm(false);
      }
    } finally {
      setTcSaving(false);
    }
  }

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center text-gray-400">Loading...</div>
  );
  if (error || !deal) return (
    <div className="min-h-screen flex items-center justify-center text-red-400">{error || "Deal not found"}</div>
  );

  const buyerProfit = deal.repair_estimate
    ? deal.arv - deal.asking_price - deal.repair_estimate
    : deal.arv - deal.asking_price;

  const repliedCampaigns = campaigns.filter(c => c.reply_body);
  const activeCampaigns = campaigns.filter(c => ["Queued", "Sent", "Replied"].includes(c.status));
  const passedCampaigns = campaigns.filter(c => c.status === "Passed");
  const contractCampaigns = campaigns.filter(c => c.status === "Contract_Pending");

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-6 py-4">
        <div className="max-w-6xl mx-auto">
          <Link href="/deals" className="text-sm text-gray-400 hover:text-gray-600 mb-2 block">← All Deals</Link>
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-xl font-semibold text-gray-900">{deal.address}</h1>
              <p className="text-sm text-gray-500 mt-0.5">{deal.city}, {deal.state} {deal.zip} · {deal.property_type} · {deal.beds}bd/{deal.baths}ba</p>
            </div>
            <span className={`px-3 py-1 rounded-full text-xs font-medium ${
              deal.status === "Available" ? "bg-green-100 text-green-700" :
              deal.status === "Campaign Launched" ? "bg-blue-100 text-blue-700" :
              deal.status === "Under Contract" ? "bg-purple-100 text-purple-700" :
              "bg-gray-100 text-gray-600"
            }`}>{deal.status}</span>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">

        {/* Deal Numbers */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: "Asking Price", value: fmt(deal.asking_price) },
            { label: "ARV", value: fmt(deal.arv) },
            { label: "Rehab Est.", value: fmt(deal.repair_estimate) },
            { label: "Buyer Profit", value: fmt(buyerProfit), highlight: true },
            { label: "Contract Price", value: fmt(deal.contract_price) },
            { label: "Assignment Fee", value: fmt(deal.asking_price - deal.contract_price) },
            { label: "My Payout", value: fmt(deal.my_payout) },
            { label: "JV Split", value: deal.jv_split_percentage ? `${deal.jv_split_percentage}%` : "—" },
          ].map(({ label, value, highlight }) => (
            <div key={label} className={`bg-white rounded-lg border p-4 ${highlight ? "border-green-200" : "border-gray-200"}`}>
              <p className="text-xs text-gray-500 mb-1">{label}</p>
              <p className={`text-lg font-semibold ${highlight ? "text-green-600" : "text-gray-900"}`}>{value}</p>
            </div>
          ))}
        </div>

        {/* Condition */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <p className="text-xs text-gray-500 mb-1">Condition</p>
          <p className="text-sm text-gray-700">{deal.condition_description || "—"}</p>
        </div>

        {/* Campaign Summary */}
        <div className="grid grid-cols-4 gap-4">
          {[
            { label: "Total Buyers", value: new Set(campaigns.map(c => c.buyer_id)).size },
            { label: "Active", value: activeCampaigns.length },
            { label: "Replied", value: repliedCampaigns.length },
            { label: "Passed", value: passedCampaigns.length },
          ].map(({ label, value }) => (
            <div key={label} className="bg-white rounded-lg border border-gray-200 p-4 text-center">
              <p className="text-2xl font-semibold text-gray-900">{value}</p>
              <p className="text-xs text-gray-500 mt-1">{label}</p>
            </div>
          ))}
        </div>

        {/* Contract Ready Alert */}
        {contractCampaigns.length > 0 && (
          <div className="bg-sky-50 border border-sky-200 rounded-lg p-4">
            <p className="text-sm font-medium text-sky-800 mb-2">🔵 Contract Ready</p>
            {contractCampaigns.map(c => {
              const b = buyers[c.buyer_id];
              return (
                <div key={c.id} className="text-sm text-sky-700 space-y-1">
                  <p><span className="font-medium">{b?.full_name || "Buyer"}</span> — {b?.email}</p>
                  {c.buyer_legal_name && <p>Legal name: {c.buyer_legal_name}</p>}
                  {c.buyer_phone && <p>Phone: {c.buyer_phone}</p>}
                  {c.buyer_title_company && <p>Title company: {c.buyer_title_company}</p>}
                  {c.agreed_price && <p>Agreed price: {fmt(c.agreed_price)}</p>}
                </div>
              );
            })}
          </div>
        )}

        {/* Title Company */}
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-medium text-gray-900">Title Company</h2>
            {!titleCompany && (
              <button onClick={() => setShowTCForm(!showTCForm)}
                className="text-xs text-blue-600 hover:text-blue-800">
                + Add
              </button>
            )}
          </div>

          {titleCompany ? (
            <div className="space-y-1 text-sm text-gray-700">
              <p className="font-medium">{titleCompany.company_name}</p>
              {titleCompany.contact_name && <p>{titleCompany.contact_name} · {titleCompany.contact_email}</p>}
              {titleCompany.contact_phone && <p>{titleCompany.contact_phone}</p>}
              {titleCompany.file_number && <p className="text-gray-500">File #: {titleCompany.file_number}</p>}
              <span className="inline-block px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs mt-1">{titleCompany.status}</span>
            </div>
          ) : showTCForm ? (
            <div className="space-y-3">
              {[
                { key: "company_name", label: "Company Name *", required: true },
                { key: "contact_name", label: "Contact Name" },
                { key: "contact_email", label: "Contact Email *", required: true },
                { key: "contact_phone", label: "Phone" },
                { key: "file_number", label: "File Number" },
              ].map(({ key, label }) => (
                <div key={key}>
                  <label className="block text-xs text-gray-500 mb-1">{label}</label>
                  <input
                    className="w-full border border-gray-200 rounded px-3 py-1.5 text-sm focus:outline-none focus:border-blue-400"
                    value={(tcForm as any)[key]}
                    onChange={e => setTcForm(prev => ({ ...prev, [key]: e.target.value }))}
                  />
                </div>
              ))}
              <div className="flex gap-2 pt-1">
                <button onClick={saveTitleCompany} disabled={tcSaving}
                  className="px-4 py-1.5 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 disabled:opacity-50">
                  {tcSaving ? "Saving..." : "Save"}
                </button>
                <button onClick={() => setShowTCForm(false)}
                  className="px-4 py-1.5 bg-gray-100 text-gray-600 text-xs rounded hover:bg-gray-200">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-400">No title company added yet.</p>
          )}
        </div>

        {/* Campaign History */}
        <div className="bg-white rounded-lg border border-gray-200">
          <div className="px-4 py-3 border-b border-gray-100">
            <h2 className="text-sm font-medium text-gray-900">Campaign History</h2>
          </div>
          <div className="divide-y divide-gray-50">
            {campaigns.length === 0 ? (
              <p className="text-sm text-gray-400 p-4">No campaigns yet.</p>
            ) : (
              campaigns.map(c => {
                const buyer = buyers[c.buyer_id];
                const isExpanded = expandedCampaign === c.id;
                const stage = c.conversation_stage || "pitching";

                return (
                  <div key={c.id}>
                    <button
                      onClick={() => setExpandedCampaign(isExpanded ? null : c.id)}
                      className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3 min-w-0">
                          <span className="text-xs text-gray-400 w-14 flex-shrink-0">Touch {c.touch_number}</span>
                          <span className={`text-xs font-medium ${statusColors[c.status] || "text-gray-500"}`}>
                            {c.status}
                          </span>
                          <span className="text-sm text-gray-700 truncate font-medium">
                            {buyer?.full_name || "Loading..."}
                          </span>
                          <span className={`px-2 py-0.5 rounded-full text-xs ${stageColors[stage] || "bg-gray-100 text-gray-500"}`}>
                            {stage}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 flex-shrink-0 ml-3">
                          {c.reply_received_at && (
                            <span className="text-xs text-green-500">Replied {fmtDate(c.reply_received_at)}</span>
                          )}
                          <span className="text-xs text-gray-400">{fmtDate(c.sent_at || c.scheduled_send_at)}</span>
                          <span className="text-gray-300 text-xs">{isExpanded ? "▲" : "▼"}</span>
                        </div>
                      </div>
                      {c.subject && (
                        <p className="text-xs text-gray-400 mt-1 ml-17 pl-14">{c.subject}</p>
                      )}
                    </button>

                    {isExpanded && (
                      <div className="px-4 pb-4 space-y-3 bg-gray-50">
                        {/* Email body */}
                        {c.body && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Email sent</p>
                            <div className="bg-white border border-gray-200 rounded p-3 text-sm text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
                              {c.body}
                            </div>
                          </div>
                        )}

                        {/* Reply */}
                        {c.reply_body && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Buyer reply</p>
                            <div className="bg-blue-50 border border-blue-100 rounded p-3 text-sm text-gray-700 whitespace-pre-wrap max-h-48 overflow-y-auto">
                              {c.reply_body.split(/\r?\nOn .{10,100}wrote:/)[0].trim()}
                            </div>
                          </div>
                        )}

                        {/* AI insights */}
                        {c.ai_extracted_insights && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">AI insight</p>
                            <p className="text-xs text-gray-600 bg-yellow-50 border border-yellow-100 rounded p-2">{c.ai_extracted_insights}</p>
                          </div>
                        )}

                        {/* Contract info collected */}
                        {(c.buyer_legal_name || c.buyer_phone || c.buyer_title_company) && (
                          <div>
                            <p className="text-xs font-medium text-gray-500 mb-1">Contract info collected</p>
                            <div className="text-xs text-gray-600 space-y-0.5">
                              {c.buyer_legal_name && <p>Legal name: {c.buyer_legal_name}</p>}
                              {c.buyer_phone && <p>Phone: {c.buyer_phone}</p>}
                              {c.buyer_title_company && <p>Title: {c.buyer_title_company}</p>}
                              {c.agreed_price && <p>Agreed price: {fmt(c.agreed_price)}</p>}
                            </div>
                          </div>
                        )}

                        {/* Pass reason */}
                        {c.pass_reason_raw && (
                          <div>
                            <p className="text-xs font-medium text-red-400 mb-1">Pass reason</p>
                            <p className="text-xs text-gray-600">{c.pass_reason_raw}</p>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

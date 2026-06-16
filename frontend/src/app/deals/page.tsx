"use client";

import { useEffect, useState, useCallback } from "react";
import { usePolling } from "@/hooks/usePolling";
import {
  Search,
  Plus,
  Eye,
  Pencil,
  Trash2,
  Rocket,
  X,
  ChevronLeft,
  ChevronRight,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  Home,
  MapPin,
  DollarSign,
  Building2,
  Users,
  TreePine,
  ClipboardList,
  Upload,
  FileText,
  Image,
  Loader2,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Deal {
  id: string;
  address: string;
  city: string | null;
  state: string | null;
  zip: string | null;
  county: string | null;
  property_type: string;
  beds: number | null;
  baths: number | null;
  sqft: number | null;
  year_built: number | null;
  occupancy_status: string | null;
  repair_estimate: number | null;
  lot_size: string | null;
  zoning: string | null;
  utilities_available: string[] | null;
  topography_access: string | null;
  condition_description: string;
  arv: number;
  asking_price: number;
  floor_price: number;
  contract_price: number;
  title_status: string;
  photos: string[] | null;
  status: string;
  assigned_buyer_id: string | null;
  jv_partner_id: string | null;
  jv_split_percentage: number | null;
  spread: number | null;
  closed_at: string | null;
  closed_price: number | null;
  net_spread: number | null;
  jv_payout: number | null;
  my_payout: number | null;
  created_at: string;
  updated_at: string;
}

interface JVPartner {
  id: string;
  name: string;
  email: string;
}

interface Buyer {
  id: string;
  full_name: string;
  email: string;
}

interface CloseDealResult {
  id: string;
  status: string;
  closed_at: string;
  closed_price: number;
  net_spread: number;
  jv_payout: number | null;
  my_payout: number | null;
  buyer_updated: boolean;
  jv_updated: boolean;
}

interface CampaignLaunchResult {
  deal_id: string;
  deal_address: string;
  total_buyers: number;
  total_campaigns_created: number;
}

interface DealFormData {
  address: string;
  city: string;
  state: string;
  zip: string;
  county: string;
  property_type: string;
  beds: string;
  baths: string;
  sqft: string;
  year_built: string;
  occupancy_status: string;
  repair_estimate: string;
  lot_size: string;
  zoning: string;
  utilities_available: string;
  topography_access: string;
  condition_description: string;
  arv: string;
  asking_price: string;
  floor_price: string;
  contract_price: string;
  title_status: string;
  photos: string;
  jv_partner_id: string;
  jv_split_percentage: string;
}

const emptyForm: DealFormData = {
  address: "",
  city: "",
  state: "",
  zip: "",
  county: "",
  property_type: "House",
  beds: "",
  baths: "",
  sqft: "",
  year_built: "",
  occupancy_status: "",
  repair_estimate: "",
  lot_size: "",
  zoning: "",
  utilities_available: "",
  topography_access: "",
  condition_description: "",
  arv: "",
  asking_price: "",
  floor_price: "",
  contract_price: "",
  title_status: "Clear",
  photos: "",
  jv_partner_id: "",
  jv_split_percentage: "50",
};

const PROPERTY_TYPES = ["House", "Land"];
const TITLE_STATUSES = ["Clear", "Liens", "Probate", "Other"];
const OCCUPANCY_STATUSES = ["", "Vacant", "Tenant", "Owner"];
const DEAL_STATUSES = ["Available", "Under Contract", "Sold", "Dead", "Campaign Launched"];

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const statusConfig: Record<string, { label: string; bg: string; dot: string }> = {
  Available: { label: "Available", bg: "bg-emerald-500/10 text-emerald-400", dot: "bg-emerald-400" },
  "Under Contract": { label: "Under Contract", bg: "bg-blue-500/10 text-blue-400", dot: "bg-blue-400" },
  Sold: { label: "Sold", bg: "bg-purple-500/10 text-purple-400", dot: "bg-purple-400" },
  Dead: { label: "Dead", bg: "bg-red-500/10 text-red-400", dot: "bg-red-400" },
  "Campaign Launched": { label: "Campaign Launched", bg: "bg-amber-500/10 text-amber-400", dot: "bg-amber-400" },
};

const propertyIcon: Record<string, React.ReactNode> = {
  House: <Home className="w-3.5 h-3.5" />,
  Land: <TreePine className="w-3.5 h-3.5" />,
};

function formatCurrency(val: number | null | undefined): string {
  if (val == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(val);
}

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

import { apiFetch } from "@/lib/api";

// ---------------------------------------------------------------------------
// Modal wrapper
// ---------------------------------------------------------------------------

function Modal({
  open,
  onClose,
  title,
  size = "lg",
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  size?: "sm" | "lg";
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

  const maxW = size === "sm" ? "max-w-sm" : "max-w-2xl";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className={`relative w-full ${maxW} max-h-[90vh] overflow-y-auto rounded-xl border border-slate-700/50 bg-slate-900 shadow-2xl`}
      >
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
    <Modal open={open} onClose={onClose} title="Delete Deal" size="sm">
      <div className="space-y-4">
        <p className="text-slate-300">
          Are you sure you want to delete the deal at{" "}
          <span className="font-semibold text-white">{name}</span>? This action cannot be undone.
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
// Campaign launch modal
// ---------------------------------------------------------------------------

function CampaignLaunchModal({
  open,
  onClose,
  onLaunch,
  launching,
  result,
}: {
  open: boolean;
  onClose: () => void;
  onLaunch: () => void;
  launching: boolean;
  result: CampaignLaunchResult | null;
}) {
  return (
    <Modal open={open} onClose={onClose} title="Launch Campaign" size="sm">
      {result ? (
        <div className="space-y-4 text-center">
          <div className="w-14 h-14 rounded-full bg-emerald-500/10 flex items-center justify-center mx-auto">
            <CheckCircle2 className="w-7 h-7 text-emerald-400" />
          </div>
          <div>
            <p className="font-semibold text-white text-lg">Campaign Launched!</p>
            <p className="text-sm text-slate-400 mt-1">{result.deal_address}</p>
          </div>
          <div className="grid grid-cols-2 gap-3 bg-slate-800/50 rounded-lg p-4">
            <div>
              <p className="text-2xl font-bold text-white">{result.total_buyers}</p>
              <p className="text-xs text-slate-500">Buyers Targeted</p>
            </div>
            <div>
              <p className="text-2xl font-bold text-white">{result.total_campaigns_created}</p>
              <p className="text-xs text-slate-500">Emails Created</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-full px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors"
          >
            Done
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-slate-300">
            This will run semantic matching against all active buyers and generate a 6-touch email
            campaign for each matched buyer. The deal status will be updated to{" "}
            <span className="font-semibold text-white">&ldquo;Campaign Launched&rdquo;</span>.
          </p>
          <div className="flex justify-end gap-3">
            <button
              onClick={onClose}
              disabled={launching}
              className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={onLaunch}
              disabled={launching}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-amber-600 hover:bg-amber-500 transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {launching ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Launching…
                </>
              ) : (
                <>
                  <Rocket className="w-4 h-4" />
                  Launch Campaign
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Deal form (shared between Add & Edit)
// ---------------------------------------------------------------------------

function DealFormSection({
  title,
  icon,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-semibold text-slate-300 border-b border-slate-700/30 pb-2">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

function DealForm({
  data,
  onChange,
  errors,
  jvPartners,
}: {
  data: DealFormData;
  onChange: (d: DealFormData) => void;
  errors: Partial<Record<keyof DealFormData, string>>;
  jvPartners: JVPartner[];
}) {
  const field =
    (key: keyof DealFormData) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
      onChange({ ...data, [key]: e.target.value });

  const inputCls =
    "w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors";
  const labelCls = "block text-sm font-medium text-slate-400 mb-1.5";
  const errorCls = "text-xs text-red-400 mt-1";
  const isHouse = data.property_type === "House";

  return (
    <div className="space-y-6">
      {/* Location */}
      <DealFormSection title="Location" icon={<MapPin className="w-4 h-4 text-blue-400" />}>
        <div>
          <label className={labelCls}>
            Address <span className="text-red-400">*</span>
          </label>
          <input className={inputCls} value={data.address} onChange={field("address")} placeholder="123 Main St" />
          {errors.address && <p className={errorCls}>{errors.address}</p>}
        </div>
        <div className="grid grid-cols-3 gap-3">
          <div className="col-span-1">
            <label className={labelCls}>City</label>
            <input className={inputCls} value={data.city} onChange={field("city")} placeholder="City" />
          </div>
          <div>
            <label className={labelCls}>State</label>
            <input className={inputCls} value={data.state} onChange={field("state")} placeholder="State" maxLength={2} />
          </div>
          <div>
            <label className={labelCls}>Zip</label>
            <input className={inputCls} value={data.zip} onChange={field("zip")} placeholder="Zip" />
          </div>
        </div>
        <div>
          <label className={labelCls}>County</label>
          <input className={inputCls} value={data.county} onChange={field("county")} placeholder="County" />
        </div>
      </DealFormSection>

      {/* Property Details */}
      <DealFormSection title="Property Details" icon={<Building2 className="w-4 h-4 text-purple-400" />}>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>
              Property Type <span className="text-red-400">*</span>
            </label>
            <div className="relative">
              <select className={`${inputCls} appearance-none`} value={data.property_type} onChange={field("property_type")}>
                {PROPERTY_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
              <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
            </div>
          </div>
          {isHouse && (
            <div>
              <label className={labelCls}>Year Built</label>
              <input className={inputCls} value={data.year_built} onChange={field("year_built")} placeholder="e.g. 1990" />
            </div>
          )}
        </div>

        {isHouse ? (
          <>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className={labelCls}>
                  Beds <span className="text-red-400">*</span>
                </label>
                <input className={inputCls} value={data.beds} onChange={field("beds")} placeholder="e.g. 3" type="number" />
                {errors.beds && <p className={errorCls}>{errors.beds}</p>}
              </div>
              <div>
                <label className={labelCls}>
                  Baths <span className="text-red-400">*</span>
                </label>
                <input className={inputCls} value={data.baths} onChange={field("baths")} placeholder="e.g. 2" type="number" step="0.5" />
                {errors.baths && <p className={errorCls}>{errors.baths}</p>}
              </div>
              <div>
                <label className={labelCls}>
                  Sq Ft <span className="text-red-400">*</span>
                </label>
                <input className={inputCls} value={data.sqft} onChange={field("sqft")} placeholder="e.g. 1500" type="number" />
                {errors.sqft && <p className={errorCls}>{errors.sqft}</p>}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls}>Occupancy Status</label>
                <div className="relative">
                  <select className={`${inputCls} appearance-none`} value={data.occupancy_status} onChange={field("occupancy_status")}>
                    {OCCUPANCY_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s || "Select…"}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
                </div>
              </div>
              <div>
                <label className={labelCls}>Repair Estimate ($)</label>
                <input className={inputCls} value={data.repair_estimate} onChange={field("repair_estimate")} placeholder="e.g. 25000" type="number" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls}>Lot Size (optional)</label>
                <input className={inputCls} value={data.lot_size} onChange={field("lot_size")} placeholder="e.g. 0.25 acres" />
              </div>
              <div></div>
            </div>
          </>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={labelCls}>
                  Lot Size <span className="text-red-400">*</span>
                </label>
                <input className={inputCls} value={data.lot_size} onChange={field("lot_size")} placeholder="e.g. 1.5 acres" />
                {errors.lot_size && <p className={errorCls}>{errors.lot_size}</p>}
              </div>
              <div>
                <label className={labelCls}>
                  Zoning <span className="text-red-400">*</span>
                </label>
                <input className={inputCls} value={data.zoning} onChange={field("zoning")} placeholder="e.g. Residential" />
                {errors.zoning && <p className={errorCls}>{errors.zoning}</p>}
              </div>
            </div>
            <div>
              <label className={labelCls}>Utilities Available</label>
              <input
                className={inputCls}
                value={data.utilities_available}
                onChange={field("utilities_available")}
                placeholder="Comma-separated, e.g. Electric, Water, Sewer"
              />
            </div>
            <div>
              <label className={labelCls}>Topography / Access</label>
              <input className={inputCls} value={data.topography_access} onChange={field("topography_access")} placeholder="e.g. Flat, road frontage" />
            </div>
          </>
        )}
      </DealFormSection>

      {/* Condition */}
      <DealFormSection title="Condition" icon={<ClipboardList className="w-4 h-4 text-amber-400" />}>
        <div>
          <label className={labelCls}>
            Condition Description <span className="text-red-400">*</span>
          </label>
          <textarea
            className={`${inputCls} min-h-[72px] resize-y`}
            value={data.condition_description}
            onChange={field("condition_description")}
            placeholder="Describe the property condition…"
          />
          {errors.condition_description && <p className={errorCls}>{errors.condition_description}</p>}
        </div>
        <div>
          <label className={labelCls}>Title Status</label>
          <div className="relative">
            <select className={`${inputCls} appearance-none`} value={data.title_status} onChange={field("title_status")}>
              {TITLE_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>
        </div>
        <div>
          <label className={labelCls}>Photos (URLs)</label>
          <input
            className={inputCls}
            value={data.photos}
            onChange={field("photos")}
            placeholder="Comma-separated URLs"
          />
        </div>
      </DealFormSection>

      {/* Financials */}
      <DealFormSection title="Financials" icon={<DollarSign className="w-4 h-4 text-emerald-400" />}>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>
              ARV <span className="text-red-400">*</span>
            </label>
            <input className={inputCls} value={data.arv} onChange={field("arv")} placeholder="e.g. 250000" type="number" />
            {errors.arv && <p className={errorCls}>{errors.arv}</p>}
          </div>
          <div>
            <label className={labelCls}>
              Asking Price <span className="text-red-400">*</span>
            </label>
            <input className={inputCls} value={data.asking_price} onChange={field("asking_price")} placeholder="e.g. 200000" type="number" />
            {errors.asking_price && <p className={errorCls}>{errors.asking_price}</p>}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>
              Floor Price <span className="text-red-400">*</span>
            </label>
            <input className={inputCls} value={data.floor_price} onChange={field("floor_price")} placeholder="e.g. 180000" type="number" />
            {errors.floor_price && <p className={errorCls}>{errors.floor_price}</p>}
          </div>
          <div>
            <label className={labelCls}>
              Contract Price <span className="text-red-400">*</span>
            </label>
            <input className={inputCls} value={data.contract_price} onChange={field("contract_price")} placeholder="e.g. 160000" type="number" />
            {errors.contract_price && <p className={errorCls}>{errors.contract_price}</p>}
          </div>
        </div>
      </DealFormSection>

      {/* JV Partner */}
      <DealFormSection title="JV Partner" icon={<Users className="w-4 h-4 text-cyan-400" />}>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={labelCls}>JV Partner</label>
            <div className="relative">
              <select className={`${inputCls} appearance-none`} value={data.jv_partner_id} onChange={field("jv_partner_id")}>
                <option value="">None</option>
                {jvPartners.map((jp) => (
                  <option key={jp.id} value={jp.id}>
                    {jp.name}
                  </option>
                ))}
              </select>
              <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
            </div>
          </div>
          <div>
            <label className={labelCls}>Split %</label>
            <input className={inputCls} value={data.jv_split_percentage} onChange={field("jv_split_percentage")} placeholder="50" type="number" min={0} max={100} />
          </div>
        </div>
      </DealFormSection>
    </div>
  );
}

// ---------------------------------------------------------------------------
// View modal
// ---------------------------------------------------------------------------

function ViewModal({
  open,
  onClose,
  deal,
  jvPartnerName,
  assignedBuyerName,
}: {
  open: boolean;
  onClose: () => void;
  deal: Deal | null;
  jvPartnerName: string;
  assignedBuyerName: string;
}) {
  if (!deal) return null;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex items-start gap-4 py-2.5 border-b border-slate-700/30 last:border-0">
      <span className="w-36 shrink-0 text-sm text-slate-500">{label}</span>
      <span className="text-sm text-white break-words">{value}</span>
    </div>
  );

  return (
    <Modal open={open} onClose={onClose} title="Deal Details">
      <div className="divide-y divide-slate-700/30">
        <Row label="Address" value={deal.address} />
        <Row label="City / State / Zip" value={`${deal.city || ""}, ${deal.state || ""} ${deal.zip || ""}`} />
        <Row label="County" value={deal.county || "—"} />
        <Row
          label="Property Type"
          value={
            <span className="inline-flex items-center gap-1.5">
              {propertyIcon[deal.property_type]}
              {deal.property_type}
            </span>
          }
        />
        {deal.property_type === "House" && (
          <>
            <Row label="Beds / Baths" value={`${deal.beds ?? "—"} / ${deal.baths ?? "—"}`} />
            <Row label="Square Feet" value={deal.sqft ? `${deal.sqft.toLocaleString()} sqft` : "—"} />
            <Row label="Year Built" value={deal.year_built ?? "—"} />
            <Row label="Occupancy" value={deal.occupancy_status || "—"} />
            <Row label="Repair Estimate" value={formatCurrency(deal.repair_estimate)} />
            <Row label="Lot Size" value={deal.lot_size || "—"} />
          </>
        )}
        {deal.property_type === "Land" && (
          <>
            <Row label="Lot Size" value={deal.lot_size || "—"} />
            <Row label="Zoning" value={deal.zoning || "—"} />
            <Row label="Utilities" value={deal.utilities_available?.join(", ") || "—"} />
            <Row label="Topography" value={deal.topography_access || "—"} />
          </>
        )}
        <Row
          label="Condition"
          value={<div className="whitespace-pre-wrap max-h-24 overflow-y-auto">{deal.condition_description}</div>}
        />
        <Row label="Title Status" value={deal.title_status} />
        <Row label="ARV" value={formatCurrency(deal.arv)} />
        <Row label="Asking Price" value={formatCurrency(deal.asking_price)} />
        <Row label="Floor Price" value={formatCurrency(deal.floor_price)} />
        <Row label="Contract Price" value={formatCurrency(deal.contract_price)} />
        <Row label="Spread" value={deal.spread != null ? formatCurrency(deal.spread) : "Pending"} />
        <Row
          label="Status"
          value={
            <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${statusConfig[deal.status]?.bg || ""}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${statusConfig[deal.status]?.dot || ""}`} />
              {deal.status}
            </span>
          }
        />
        <Row label="Assigned Buyer" value={assignedBuyerName || "—"} />
        <Row label="JV Partner" value={jvPartnerName || "—"} />
        <Row label="JV Split" value={deal.jv_split_percentage != null ? `${deal.jv_split_percentage}%` : "—"} />
        {deal.photos && deal.photos.length > 0 && (
          <Row
            label="Photos"
            value={
              <div className="flex flex-wrap gap-2">
                {deal.photos.map((url, i) => (
                  <a
                    key={i}
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 hover:text-blue-300 underline text-xs"
                  >
                    Photo {i + 1}
                  </a>
                ))}
              </div>
            }
          />
        )}
        <Row label="Created" value={formatDate(deal.created_at)} />
        <Row label="Updated" value={formatDate(deal.updated_at)} />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DealsPage() {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [jvPartners, setJvPartners] = useState<JVPartner[]>([]);
  const [buyers, setBuyers] = useState<Buyer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Search & filter
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");

  // Pagination
  const [page, setPage] = useState(0);
  const perPage = 10;

  // Modal state
  const [addOpen, setAddOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Deal | null>(null);
  const [viewTarget, setViewTarget] = useState<Deal | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Deal | null>(null);
  const [campaignTarget, setCampaignTarget] = useState<Deal | null>(null);
  const [campaignResult, setCampaignResult] = useState<CampaignLaunchResult | null>(null);
  const [closeTarget, setCloseTarget] = useState<Deal | null>(null);
  const [closeResult, setCloseResult] = useState<CloseDealResult | null>(null);
  const [closePrice, setClosePrice] = useState("");
  const [closing, setClosing] = useState(false);
  const [ucTarget, setUcTarget] = useState<Deal | null>(null);
  const [ucBuyerId, setUcBuyerId] = useState("");
  const [ucSubmitting, setUcSubmitting] = useState(false);

  // Form state
  const [form, setForm] = useState<DealFormData>(emptyForm);
  const [formErrors, setFormErrors] = useState<Partial<Record<keyof DealFormData, string>>>({});
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);

  // -----------------------------------------------------------------------
  // Data fetching
  // -----------------------------------------------------------------------

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dealsData, jvData, buyersData] = await Promise.all([
        apiFetch<Deal[]>("/api/deals"),
        apiFetch<JVPartner[]>("/api/jv-partners"),
        apiFetch<Buyer[]>("/api/buyers"),
      ]);
      setDeals(dealsData);
      setJvPartners(jvData);
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
  const jvPartnerMap = new Map(jvPartners.map((jp) => [jp.id, jp.name]));
  const buyerMap = new Map(buyers.map((b) => [b.id, b.full_name]));

  // -----------------------------------------------------------------------
  // Filtering
  // -----------------------------------------------------------------------

  const filtered = deals.filter((d) => {
    const q = search.toLowerCase();
    if (q) {
      const addressMatch = d.address.toLowerCase().includes(q);
      const cityMatch = d.city?.toLowerCase().includes(q);
      const buyerName = (d.assigned_buyer_id ? buyerMap.get(d.assigned_buyer_id) || "" : "").toLowerCase().includes(q);
      if (!addressMatch && !cityMatch && !buyerName) return false;
    }
    if (typeFilter && d.property_type !== typeFilter) return false;
    if (statusFilter && d.status !== statusFilter) return false;
    return true;
  });

  const totalPages = Math.max(1, Math.ceil(filtered.length / perPage));
  const safePage = Math.min(page, totalPages - 1);
  const paged = filtered.slice(safePage * perPage, safePage * perPage + perPage);

  // Reset page when filters change
  useEffect(() => {
    setPage(0);
  }, [search, typeFilter, statusFilter]);

  // -----------------------------------------------------------------------
  // Validation
  // -----------------------------------------------------------------------

  function validate(data: DealFormData): Partial<Record<keyof DealFormData, string>> {
    const errs: Partial<Record<keyof DealFormData, string>> = {};
    if (!data.address.trim()) errs.address = "Address is required";
    if (!data.condition_description.trim()) errs.condition_description = "Condition description is required";
    if (!data.arv.trim() || isNaN(Number(data.arv))) errs.arv = "Valid ARV is required";
    if (!data.asking_price.trim() || isNaN(Number(data.asking_price))) errs.asking_price = "Valid asking price is required";
    if (!data.floor_price.trim() || isNaN(Number(data.floor_price))) errs.floor_price = "Valid floor price is required";
    if (!data.contract_price.trim() || isNaN(Number(data.contract_price))) errs.contract_price = "Valid contract price is required";

    if (data.property_type === "House") {
      if (!data.beds.trim() || isNaN(Number(data.beds))) errs.beds = "Beds is required";
      if (!data.baths.trim() || isNaN(Number(data.baths))) errs.baths = "Baths is required";
      if (!data.sqft.trim() || isNaN(Number(data.sqft))) errs.sqft = "Sq ft is required";
    } else {
      if (!data.lot_size.trim()) errs.lot_size = "Lot size is required";
      if (!data.zoning.trim()) errs.zoning = "Zoning is required";
    }

    return errs;
  }

  function buildPayload(data: DealFormData): Record<string, unknown> {
    const payload: Record<string, unknown> = {
      address: data.address.trim(),
      city: data.city.trim() || null,
      state: data.state.trim() || null,
      zip: data.zip.trim() || null,
      county: data.county.trim() || null,
      property_type: data.property_type,
      condition_description: data.condition_description.trim(),
      arv: Number(data.arv),
      asking_price: Number(data.asking_price),
      floor_price: Number(data.floor_price),
      contract_price: Number(data.contract_price),
      title_status: data.title_status,
      photos: data.photos.trim()
        ? data.photos.split(",").map((s) => s.trim()).filter(Boolean)
        : null,
      jv_partner_id: data.jv_partner_id || null,
      jv_split_percentage: data.jv_split_percentage ? Number(data.jv_split_percentage) : 50,
    };

    if (data.property_type === "House") {
      payload.beds = data.beds ? Number(data.beds) : null;
      payload.baths = data.baths ? Number(data.baths) : null;
      payload.sqft = data.sqft ? Number(data.sqft) : null;
      payload.year_built = data.year_built ? Number(data.year_built) : null;
      payload.occupancy_status = data.occupancy_status || null;
      payload.repair_estimate = data.repair_estimate ? Number(data.repair_estimate) : null;
      payload.lot_size = data.lot_size.trim() || null;
    } else {
      payload.lot_size = data.lot_size.trim() || null;
      payload.zoning = data.zoning.trim() || null;
      payload.utilities_available = data.utilities_available.trim()
        ? data.utilities_available.split(",").map((s) => s.trim()).filter(Boolean)
        : null;
      payload.topography_access = data.topography_access.trim() || null;
    }

    return payload;
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
      const created = await apiFetch<Deal>("/api/deals", {
        method: "POST",
        body: JSON.stringify(buildPayload(form)),
      });

      // Upload files if any were selected
      if (selectedFiles.length > 0) {
        setUploading(true);
        setUploadProgress(`Uploading ${selectedFiles.length} file(s)…`);
        try {
          const formData = new FormData();
          selectedFiles.forEach((f) => formData.append("files", f));

          const uploadRes = await fetch(`/api/deals/${created.id}/files`, {
            method: "POST",
            body: formData,
          });

          if (!uploadRes.ok) {
            const errBody = await uploadRes.json().catch(() => ({}));
            throw new Error(errBody.detail || "File upload failed");
          }

          const result = await uploadRes.json();
          setUploadProgress(`${result.uploaded} file(s) uploaded successfully`);
          setTimeout(() => setUploadProgress(null), 3000);
        } catch (uploadErr: any) {
          setFormErrors({ address: `Deal created but file upload failed: ${uploadErr.message}` });
        } finally {
          setUploading(false);
        }
      }

      setAddOpen(false);
      setForm(emptyForm);
      setSelectedFiles([]);
      setFormErrors({});
      await loadData();
    } catch (err: any) {
      setFormErrors({ address: err.message });
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
      await apiFetch(`/api/deals/${editTarget.id}`, {
        method: "PUT",
        body: JSON.stringify(buildPayload(form)),
      });
      setEditTarget(null);
      setForm(emptyForm);
      setFormErrors({});
      await loadData();
    } catch (err: any) {
      setFormErrors({ address: err.message });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await apiFetch(`/api/deals/${deleteTarget.id}`, {
        method: "DELETE",
      });
      setDeleteTarget(null);
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  async function handleLaunchCampaign() {
    if (!campaignTarget) return;
    setLaunching(true);
    setCampaignResult(null);
    try {
      const result = await apiFetch<CampaignLaunchResult>(
        `/api/campaigns/${campaignTarget.id}/launch`,
        { method: "POST" }
      );
      setCampaignResult(result);
      await loadData();
    } catch (err: any) {
      setError(err.message);
      setCampaignTarget(null);
    } finally {
      setLaunching(false);
    }
  }

  async function handleMarkUnderContract() {
    if (!ucTarget) return;
    setUcSubmitting(true);
    try {
      await apiFetch(`/api/deals/${ucTarget.id}/under-contract`, {
        method: "POST",
        body: JSON.stringify({ assigned_buyer_id: ucBuyerId || null }),
      });
      setUcTarget(null);
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setUcSubmitting(false);
    }
  }

  async function handleCloseDeal() {
    if (!closeTarget) return;
    const price = Number(closePrice);
    if (isNaN(price) || price <= 0) {
      setError("Please enter a valid closed price");
      return;
    }
    setClosing(true);
    setCloseResult(null);
    try {
      const result = await apiFetch<CloseDealResult>(`/api/deals/${closeTarget.id}/close`, {
        method: "POST",
        body: JSON.stringify({ closed_price: price }),
      });
      setCloseResult(result);
      await loadData();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setClosing(false);
    }
  }

  function openEdit(d: Deal) {
    setEditTarget(d);
    setForm({
      address: d.address,
      city: d.city || "",
      state: d.state || "",
      zip: d.zip || "",
      county: d.county || "",
      property_type: d.property_type,
      beds: d.beds?.toString() || "",
      baths: d.baths?.toString() || "",
      sqft: d.sqft?.toString() || "",
      year_built: d.year_built?.toString() || "",
      occupancy_status: d.occupancy_status || "",
      repair_estimate: d.repair_estimate?.toString() || "",
      lot_size: d.lot_size || "",
      zoning: d.zoning || "",
      utilities_available: d.utilities_available?.join(", ") || "",
      topography_access: d.topography_access || "",
      condition_description: d.condition_description,
      arv: d.arv.toString(),
      asking_price: d.asking_price.toString(),
      floor_price: d.floor_price.toString(),
      contract_price: d.contract_price.toString(),
      title_status: d.title_status,
      photos: d.photos?.join(", ") || "",
      jv_partner_id: d.jv_partner_id || "",
      jv_split_percentage: d.jv_split_percentage?.toString() || "50",
    });
    setFormErrors({});
  }

  function openAdd() {
    setForm(emptyForm);
    setFormErrors({});
    setSelectedFiles([]);
    setUploadProgress(null);
    setAddOpen(true);
  }

  function openCampaign(d: Deal) {
    setCampaignTarget(d);
    setCampaignResult(null);
  }

  // -----------------------------------------------------------------------
  // Spread color
  // -----------------------------------------------------------------------

  function spreadColor(spread: number | null) {
    if (spread == null) return "text-slate-500";
    if (spread >= 50000) return "text-emerald-400";
    if (spread >= 20000) return "text-amber-400";
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
            <h1 className="text-2xl font-bold text-white tracking-tight">Deals</h1>
            <p className="text-sm text-slate-500 mt-0.5">
              {filtered.length} deal{filtered.length !== 1 ? "s" : ""}
              {filtered.length !== deals.length && ` (filtered from ${deals.length})`}
            </p>
          </div>
          <button
            onClick={openAdd}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 active:bg-blue-600 transition-colors shadow-lg shadow-blue-600/20"
          >
            <Plus className="w-4 h-4" />
            Add Deal
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
              placeholder="Search by address, city, or buyer…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <div className="relative">
            <select
              className="appearance-none pl-3 pr-8 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500/50 transition-colors"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
            >
              <option value="">All Types</option>
              {PROPERTY_TYPES.map((t) => (
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
              {DEAL_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <ChevronDown className="absolute right-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
          </div>

          {(search || typeFilter || statusFilter) && (
            <button
              onClick={() => {
                setSearch("");
                setTypeFilter("");
                setStatusFilter("");
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
                {search || typeFilter || statusFilter ? "No deals match your filters" : "No deals yet"}
              </p>
              <p className="text-slate-600 text-xs mt-1">
                {search || typeFilter || statusFilter
                  ? "Try adjusting your search or filter criteria"
                  : "Add your first deal to get started"}
              </p>
            </div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Address</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Type</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Status</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">ARV</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Asking</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Spread</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Assigned Buyer</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">JV Partner</th>
                      <th className="text-right px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wider">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/30">
                    {paged.map((d, i) => (
                      <tr key={d.id} className="group hover:bg-slate-800/30 transition-colors" style={{ animation: `fadeIn 0.3s ease-out ${i * 30}ms forwards`, opacity: 0 }}>
                        <td className="px-4 py-3">
                          <div className="max-w-[220px]">
                            <p className="font-medium text-white truncate">{d.address}</p>
                            {d.city && (
                              <p className="text-xs text-slate-500 truncate">
                                {d.city}{d.state ? `, ${d.state}` : ""}
                              </p>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-700/50 text-slate-300">
                            {propertyIcon[d.property_type]}
                            {d.property_type}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-medium ${statusConfig[d.status]?.bg || ""}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${statusConfig[d.status]?.dot || ""}`} />
                            {d.status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">
                          {formatCurrency(d.arv)}
                        </td>
                        <td className="px-4 py-3 text-right text-slate-300 tabular-nums">
                          {formatCurrency(d.asking_price)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span className={`tabular-nums font-medium ${spreadColor(d.spread)}`}>
                            {d.spread != null ? formatCurrency(d.spread) : "—"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-slate-400 max-w-[140px]">
                          <span className="truncate block">
                            {d.assigned_buyer_id ? buyerMap.get(d.assigned_buyer_id) || "—" : "—"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-slate-400 max-w-[140px]">
                          <span className="truncate block">
                            {d.jv_partner_id ? jvPartnerMap.get(d.jv_partner_id) || "—" : "—"}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <div className="inline-flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={() => setViewTarget(d)}
                              className="p-1.5 rounded-md text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
                              title="View details"
                            >
                              <Eye className="w-4 h-4" />
                            </button>
                            <button
                              onClick={() => openEdit(d)}
                              className="p-1.5 rounded-md text-slate-400 hover:text-blue-400 hover:bg-slate-700 transition-colors"
                              title="Edit deal"
                            >
                              <Pencil className="w-4 h-4" />
                            </button>
                            {(d.status === "Available" || d.status === "Campaign Launched") && (
                              <button
                                onClick={() => { setUcTarget(d); setUcBuyerId(d.assigned_buyer_id || ""); }}
                                className="p-1.5 rounded-md text-slate-400 hover:text-blue-400 hover:bg-slate-700 transition-colors"
                                title="Mark Under Contract"
                              >
                                <FileText className="w-4 h-4" />
                              </button>
                            )}
                            {d.status === "Available" && (
                              <button
                                onClick={() => openCampaign(d)}
                                className="p-1.5 rounded-md text-slate-400 hover:text-amber-400 hover:bg-slate-700 transition-colors"
                                title="Launch campaign"
                              >
                                <Rocket className="w-4 h-4" />
                              </button>
                            )}
                            {(d.status === "Under Contract" || d.status === "Available") && (
                              <button
                                onClick={() => {
                                  setCloseTarget(d);
                                  setClosePrice(d.asking_price?.toString() || "");
                                  setCloseResult(null);
                                }}
                                className="p-1.5 rounded-md text-slate-400 hover:text-emerald-400 hover:bg-slate-700 transition-colors"
                                title="I Got Paid — Close deal"
                              >
                                <DollarSign className="w-4 h-4" />
                              </button>
                            )}
                            <button
                              onClick={() => setDeleteTarget(d)}
                              className="p-1.5 rounded-md text-slate-400 hover:text-red-400 hover:bg-slate-700 transition-colors"
                              title="Delete deal"
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
      <Modal open={addOpen} onClose={() => { setAddOpen(false); setSelectedFiles([]); setUploadProgress(null); }} title="Add Deal">
        <DealForm data={form} onChange={setForm} errors={formErrors} jvPartners={jvPartners} />
        {/* File Upload Area */}
        <div className="mt-6 space-y-2">
          <label className="block text-sm font-medium text-slate-400 mb-1">
            Upload Photos / Documents
          </label>
          <div className="relative">
            <input
              type="file"
              multiple
              accept="image/*,.pdf,.doc,.docx,.xls,.xlsx"
              onChange={(e) => {
                const files = Array.from(e.target.files || []);
                setSelectedFiles(files);
              }}
              className="w-full text-sm text-slate-400 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-medium file:bg-blue-600/10 file:text-blue-400 hover:file:bg-blue-600/20 file:cursor-pointer cursor-pointer"
            />
          </div>
          {selectedFiles.length > 0 && (
            <div className="flex flex-wrap gap-2 mt-2">
              {selectedFiles.map((f, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-slate-800 border border-slate-700/50 text-xs text-slate-300"
                >
                  {f.type.startsWith("image/") ? (
                    <Image className="w-3 h-3" />
                  ) : (
                    <FileText className="w-3 h-3" />
                  )}
                  {f.name}
                  <button
                    onClick={() => {
                      const updated = selectedFiles.filter((_, j) => j !== i);
                      setSelectedFiles(updated);
                    }}
                    className="ml-1 text-slate-500 hover:text-red-400 transition-colors"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
            </div>
          )}
          {uploadProgress && (
            <div className="flex items-center gap-2 text-xs text-blue-400 bg-blue-500/10 px-3 py-2 rounded-lg border border-blue-500/20">
              <Loader2 className="w-3 h-3 animate-spin" />
              {uploadProgress}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-3 mt-6 pt-4 border-t border-slate-700/50">
          <button
            onClick={() => {
              setAddOpen(false);
              setSelectedFiles([]);
              setUploadProgress(null);
            }}
            disabled={saving || uploading}
            className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleCreate}
            disabled={saving || uploading}
            className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {uploading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Uploading…
              </>
            ) : saving ? (
              <>
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Saving…
              </>
            ) : (
              <>
                <Upload className="w-4 h-4" />
                Create Deal
                {selectedFiles.length > 0 && ` (${selectedFiles.length} file${selectedFiles.length !== 1 ? "s" : ""})`}
              </>
            )}
          </button>
        </div>
      </Modal>

      {/* Edit Modal */}
      <Modal open={!!editTarget} onClose={() => { setEditTarget(null); setForm(emptyForm); }} title="Edit Deal">
        <DealForm data={form} onChange={setForm} errors={formErrors} jvPartners={jvPartners} />
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
      <ViewModal
        open={!!viewTarget}
        onClose={() => setViewTarget(null)}
        deal={viewTarget}
        jvPartnerName={viewTarget?.jv_partner_id ? jvPartnerMap.get(viewTarget.jv_partner_id) || "" : ""}
        assignedBuyerName={viewTarget?.assigned_buyer_id ? buyerMap.get(viewTarget.assigned_buyer_id) || "" : ""}
      />

      {/* Delete Confirm */}
      <DeleteConfirm
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        name={deleteTarget?.address || ""}
        onConfirm={handleDelete}
        deleting={deleting}
      />

      {/* Campaign Launch Modal */}
      <CampaignLaunchModal
        open={!!campaignTarget || !!campaignResult}
        onClose={() => { setCampaignTarget(null); setCampaignResult(null); }}
        onLaunch={handleLaunchCampaign}
        launching={launching}
        result={campaignResult}
      />

      {/* Under Contract Modal */}
      <Modal
        open={!!ucTarget}
        onClose={() => setUcTarget(null)}
        title="Mark Under Contract"
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-slate-300 text-sm">
            Mark <span className="font-semibold text-white">{ucTarget?.address}</span> as Under Contract.
            This will pause any active campaigns for this deal.
          </p>
          <div>
            <label className="block text-sm font-medium text-slate-400 mb-1.5">Assign Buyer (optional)</label>
            <div className="relative">
              <select
                className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white appearance-none focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                value={ucBuyerId}
                onChange={(e) => setUcBuyerId(e.target.value)}
              >
                <option value="">No buyer selected</option>
                {buyers.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.full_name} ({b.email})
                  </option>
                ))}
              </select>
              <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
            </div>
          </div>
          <div className="flex justify-end gap-3 pt-2">
            <button
              onClick={() => setUcTarget(null)}
              disabled={ucSubmitting}
              className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleMarkUnderContract}
              disabled={ucSubmitting}
              className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors disabled:opacity-50 flex items-center gap-2"
            >
              {ucSubmitting ? (
                <>
                  <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Saving…
                </>
              ) : (
                "Mark Under Contract"
              )}
            </button>
          </div>
        </div>
      </Modal>

      {/* I Got Paid — Close Deal Modal */}
      <Modal
        open={!!closeTarget || !!closeResult}
        onClose={() => { setCloseTarget(null); setCloseResult(null); }}
        title={closeResult ? "Deal Closed! \u{1F389}" : "I Got Paid"}
        size="sm"
      >
        {closeResult ? (
          <div className="space-y-4">
            <div className="w-14 h-14 rounded-full bg-emerald-500/10 flex items-center justify-center mx-auto">
              <DollarSign className="w-7 h-7 text-emerald-400" />
            </div>
            <div className="text-center">
              <p className="font-semibold text-white text-lg">Paid!</p>
              <p className="text-sm text-slate-400 mt-1">{closeTarget?.address}</p>
            </div>
            <div className="grid grid-cols-2 gap-3 bg-slate-800/50 rounded-lg p-4">
              <div>
                <p className="text-xs text-slate-500">Closed Price</p>
                <p className="text-lg font-bold text-white">{formatCurrency(closeResult.closed_price)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">Net Spread</p>
                <p className="text-lg font-bold text-emerald-400">{formatCurrency(closeResult.net_spread)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">My Payout</p>
                <p className="text-lg font-bold text-green-400">{formatCurrency(closeResult.my_payout)}</p>
              </div>
              <div>
                <p className="text-xs text-slate-500">JV Payout</p>
                <p className="text-lg font-bold text-cyan-400">{formatCurrency(closeResult.jv_payout)}</p>
              </div>
            </div>
            <div className="flex flex-wrap gap-2 justify-center text-xs text-slate-500">
              {closeResult.buyer_updated && (
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-emerald-500/10 text-emerald-400">
                  <CheckCircle2 className="w-3 h-3" />
                  Buyer stats updated
                </span>
              )}
              {closeResult.jv_updated && (
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-blue-500/10 text-blue-400">
                  <CheckCircle2 className="w-3 h-3" />
                  JV stats updated
                </span>
              )}
            </div>
            <button
              onClick={() => { setCloseTarget(null); setCloseResult(null); }}
              className="w-full px-4 py-2 rounded-lg text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors"
            >
              Done
            </button>
          </div>
        ) : (
          <div className="space-y-4">
            <p className="text-slate-300 text-sm">
              Record payment for <span className="font-semibold text-white">{closeTarget?.address}</span>.
              This will mark the deal as Sold, calculate payouts, and update buyer/JV partner stats.
            </p>
            <div>
              <label className="block text-sm font-medium text-slate-400 mb-1.5">
                Closed Price <span className="text-red-400">*</span>
              </label>
              <input
                type="number"
                className="w-full px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/50 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 focus:border-emerald-500/50 transition-colors"
                value={closePrice}
                onChange={(e) => setClosePrice(e.target.value)}
                placeholder="Enter closed price (defaults to asking price)"
              />
            </div>
            {closeTarget && (
              <div className="grid grid-cols-2 gap-3 bg-slate-800/50 rounded-lg p-3 text-xs">
                <div>
                  <span className="text-slate-500">Asking Price:</span>
                  <span className="text-white ml-1">{formatCurrency(closeTarget.asking_price)}</span>
                </div>
                <div>
                  <span className="text-slate-500">Contract Price:</span>
                  <span className="text-white ml-1">{formatCurrency(closeTarget.contract_price)}</span>
                </div>
                <div>
                  <span className="text-slate-500">JV Split:</span>
                  <span className="text-white ml-1">{closeTarget.jv_split_percentage ?? 50}%</span>
                </div>
                <div>
                  <span className="text-slate-500">Est. Net Spread:</span>
                  <span className="text-emerald-400 ml-1 font-medium">
                    {formatCurrency(
                      (Number(closePrice) || closeTarget.asking_price) -
                      closeTarget.contract_price
                    )}
                  </span>
                </div>
              </div>
            )}
            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={() => setCloseTarget(null)}
                disabled={closing}
                className="px-4 py-2 rounded-lg text-sm font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 transition-colors disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleCloseDeal}
                disabled={closing || !closePrice || isNaN(Number(closePrice)) || Number(closePrice) <= 0}
                className="px-4 py-2 rounded-lg text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-500 transition-colors disabled:opacity-50 flex items-center gap-2"
              >
                {closing ? (
                  <>
                    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                    Closing…
                  </>
                ) : (
                  <>
                    <DollarSign className="w-4 h-4" />
                    I Got Paid — Close Deal
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

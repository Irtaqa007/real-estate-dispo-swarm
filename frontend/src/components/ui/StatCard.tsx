"use client";

import { TrendingUp, TrendingDown } from "lucide-react";

interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: React.ReactNode;
  trend?: { value: string; up: boolean };
  color?: "blue" | "emerald" | "amber" | "purple" | "red" | "cyan";
  delay?: number;
}

const colorMap: Record<string, { bg: string; icon: string; glow: string }> = {
  blue: {
    bg: "bg-blue-500/10 group-hover:bg-blue-500/15",
    icon: "text-blue-400",
    glow: "shadow-blue-500/5",
  },
  emerald: {
    bg: "bg-emerald-500/10 group-hover:bg-emerald-500/15",
    icon: "text-emerald-400",
    glow: "shadow-emerald-500/5",
  },
  amber: {
    bg: "bg-amber-500/10 group-hover:bg-amber-500/15",
    icon: "text-amber-400",
    glow: "shadow-amber-500/5",
  },
  purple: {
    bg: "bg-purple-500/10 group-hover:bg-purple-500/15",
    icon: "text-purple-400",
    glow: "shadow-purple-500/5",
  },
  red: {
    bg: "bg-red-500/10 group-hover:bg-red-500/15",
    icon: "text-red-400",
    glow: "shadow-red-500/5",
  },
  cyan: {
    bg: "bg-cyan-500/10 group-hover:bg-cyan-500/15",
    icon: "text-cyan-400",
    glow: "shadow-cyan-500/5",
  },
};

export default function StatCard({
  title,
  value,
  subtitle,
  icon,
  trend,
  color = "blue",
  delay = 0,
}: StatCardProps) {
  const c = colorMap[color];
  const delayMs = `${delay}ms`;

  return (
    <div
      className="group rounded-xl border border-slate-800/50 bg-slate-900/50 p-5 space-y-3 
                 transition-all duration-300 hover:border-slate-700/50 hover:bg-slate-800/50 
                 hover:shadow-lg hover:-translate-y-0.5"
      style={{ animation: `fadeIn 0.4s ease-out ${delayMs} forwards`, opacity: 0 }}
    >
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
          {title}
        </span>
        <div
          className={`w-9 h-9 rounded-lg flex items-center justify-center transition-colors duration-300 ${c.bg}`}
        >
          <span className={c.icon}>{icon}</span>
        </div>
      </div>
      <div>
        <p className="text-2xl font-bold text-white tracking-tight tabular-nums">{value}</p>
        {subtitle && (
          <p className="text-xs text-slate-500 mt-0.5">{subtitle}</p>
        )}
      </div>
      {trend && (
        <div className="flex items-center gap-1 text-xs pt-1 border-t border-slate-800/30">
          {trend.up ? (
            <TrendingUp className="w-3 h-3 text-emerald-400" />
          ) : (
            <TrendingDown className="w-3 h-3 text-red-400" />
          )}
          <span className={trend.up ? "text-emerald-400" : "text-red-400"}>
            {trend.value}
          </span>
        </div>
      )}
    </div>
  );
}

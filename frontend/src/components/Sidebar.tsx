"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Users, Home, LayoutDashboard, Building2, Send, Activity, BarChart3, ChevronRight } from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
  { href: "/buyers", label: "Buyers", icon: Users },
  { href: "/deals", label: "Deals", icon: Building2 },
  { href: "/jv-partners", label: "JV Partners", icon: Activity },
  { href: "/campaigns", label: "Campaigns", icon: Send },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-60 shrink-0 border-r border-slate-800/50 bg-slate-900/50 backdrop-blur-sm flex flex-col">
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 py-5 border-b border-slate-800/50">
        <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-blue-500 via-purple-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-500/20">
          <Home className="w-4 h-4 text-white" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-bold text-white tracking-tight">Dispo Swarm</p>
          <p className="text-[10px] text-slate-500 font-medium">Real Estate AI</p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {navItems.map((item) => {
          const isActive = pathname === item.href;
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`group relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all duration-200 ${
                isActive
                  ? "text-blue-400 bg-blue-600/10"
                  : "text-slate-400 hover:text-white hover:bg-slate-800/50"
              }`}
            >
              {/* Active indicator */}
              {isActive && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 rounded-full bg-blue-500 shadow-sm shadow-blue-500/50" />
              )}
              <Icon className={`w-4 h-4 shrink-0 transition-transform duration-200 ${
                isActive ? "" : "group-hover:scale-110"
              }`} />
              <span>{item.label}</span>
              {isActive && (
                <ChevronRight className="w-3.5 h-3.5 ml-auto text-blue-400/50" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-slate-800/50">
        <div className="flex items-center gap-2">
          <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse-slow" />
          <p className="text-[10px] text-slate-600 font-medium">System Online</p>
        </div>
      </div>
    </aside>
  );
}

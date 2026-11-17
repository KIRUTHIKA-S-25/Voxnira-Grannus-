import React from 'react';
import { ShieldAlert, PhoneCall } from 'lucide-react';

export function AdvisoryBanner() {
  return (
    <aside
      aria-label="Clinical safety and emergency advisory"
      className="bg-slate-900 text-slate-100 text-xs py-2 px-4 text-center flex flex-wrap items-center justify-center gap-2 border-b border-slate-800 shadow-sm z-50 sticky top-0"
    >
      <div className="flex items-center gap-1.5 font-medium text-emerald-400">
        <ShieldAlert className="w-3.5 h-3.5" />
        <span>National Telehealth Network</span>
      </div>
      <span className="text-slate-300">
        • For acute life-threatening emergencies, dial
      </span>
      <div className="inline-flex items-center gap-2">
        <a
          href="tel:108"
          className="inline-flex items-center gap-1 font-bold text-emerald-400 hover:text-emerald-300 underline"
        >
          <PhoneCall className="w-3 h-3" /> 108 (Ambulance)
        </a>
        <span className="text-slate-400">or</span>
        <a
          href="tel:112"
          className="inline-flex items-center gap-1 font-bold text-emerald-400 hover:text-emerald-300 underline"
        >
          112 (National ER)
        </a>
      </div>
      <span className="text-slate-400 hidden sm:inline">
        • All clinical triage is reviewed by registered medical practitioners.
      </span>
    </aside>
  );
}

export const DemoBanner = AdvisoryBanner;

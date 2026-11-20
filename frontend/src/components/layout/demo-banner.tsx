import React from 'react';
import { ShieldAlert, PhoneCall } from 'lucide-react';

export function AdvisoryBanner() {
  return (
    <aside
      aria-label="Demo notice and emergency contacts"
      className="bg-amber-950 text-amber-100 text-xs py-2 px-4 text-center flex flex-wrap items-center justify-center gap-2 border-b border-amber-900 shadow-sm z-50 sticky top-0"
    >
      <div className="flex items-center gap-1.5 font-bold text-amber-300">
        <ShieldAlert className="w-3.5 h-3.5" />
        <span>Demo: synthetic data, not medical advice</span>
      </div>
      <span className="text-amber-200/90 hidden sm:inline">
        • For medical emergencies, dial
      </span>
      <div className="inline-flex items-center gap-2">
        <a
          href="tel:108"
          className="inline-flex items-center gap-1 font-bold text-amber-300 hover:text-white underline"
        >
          <PhoneCall className="w-3 h-3" /> 108 (Ambulance)
        </a>
        <span className="text-amber-200/80">or</span>
        <a
          href="tel:112"
          className="inline-flex items-center gap-1 font-bold text-amber-300 hover:text-white underline"
        >
          112 (ER)
        </a>
      </div>
    </aside>
  );
}

export const DemoBanner = AdvisoryBanner;

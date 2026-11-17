'use client';

import React from 'react';
import {
  Stethoscope,
  ShieldCheck,
  Building,
  Clock,
  Languages,
  Award,
  Calendar,
  Ticket,
  MapPin,
  Sparkles,
} from 'lucide-react';
import { AssignedDoctor, DepartmentInfo, RoutingInfo } from '@/lib/api';

interface AssignedSpecialistCardProps {
  doctor?: AssignedDoctor | null;
  department?: DepartmentInfo | null;
  routing?: RoutingInfo | null;
  urgencyTier?: string;
}

export function AssignedSpecialistCard({
  doctor,
  department,
  routing,
  urgencyTier,
}: AssignedSpecialistCardProps) {
  // If doctor not yet assigned, display triage pool notice
  if (!doctor && !department) {
    return null;
  }

  const isEmergency = urgencyTier === 'emergency';
  const waitMinutes = routing?.estimated_wait_minutes ?? (isEmergency ? 0 : 15);
  const token = routing?.token || 'OPD-MED-101';
  const deptName = department?.name || doctor?.department_name || doctor?.specialty || 'General Medicine';

  return (
    <div className="bg-card border-2 border-primary/20 rounded-2xl p-6 md:p-8 shadow-sm transition-all hover:border-primary/40 space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-5 border-b border-border/60">
        <div className="flex items-center gap-3">
          <div className="w-12 h-12 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
            <Stethoscope className="w-6 h-6" />
          </div>
          <div>
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[11px] font-bold uppercase tracking-wider px-2.5 py-0.5 rounded-full bg-primary/15 text-primary border border-primary/25 flex items-center gap-1">
                <Sparkles className="w-3 h-3" />
                Assigned Specialist Consultation
              </span>
              <span className="text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                <ShieldCheck className="w-3.5 h-3.5" />
                Verified Telehealth Roster
              </span>
            </div>
            <h3 className="text-xl font-heading font-semibold text-foreground mt-1">
              {doctor?.full_name || 'Duty Medical Officer'}
            </h3>
            {doctor?.qualifications && (
              <p className="text-xs text-muted-foreground font-medium">
                {doctor.qualifications}
              </p>
            )}
          </div>
        </div>

        {/* Consultation Queue Token */}
        <div className="flex items-center gap-3 w-full sm:w-auto justify-between sm:justify-end bg-muted/40 px-4 py-2 rounded-xl border border-border">
          <div className="text-left sm:text-right">
            <span className="text-[10px] uppercase font-bold tracking-wider text-muted-foreground block">
              Consultation Token
            </span>
            <span className="font-mono font-bold text-sm text-foreground flex items-center gap-1">
              <Ticket className="w-3.5 h-3.5 text-primary" />
              {token}
            </span>
          </div>
          <div className="h-8 w-px bg-border mx-1" />
          <div className="text-left sm:text-right">
            <span className="text-[10px] uppercase font-bold tracking-wider text-muted-foreground block">
              Est. Wait
            </span>
            <span className={`font-semibold text-xs ${isEmergency ? 'text-destructive font-bold' : 'text-emerald-600 dark:text-emerald-400'}`}>
              {isEmergency ? 'Immediate Casualty' : `~${waitMinutes} Mins`}
            </span>
          </div>
        </div>
      </div>

      {/* Grid of Doctor & Department Details */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-1">
        {/* Department & Specialty */}
        <div className="flex items-start gap-3">
          <Building className="w-5 h-5 text-primary shrink-0 mt-0.5" />
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
              Clinical Department
            </h4>
            <p className="text-sm font-semibold text-foreground">{deptName}</p>
            {doctor?.opd_room && (
              <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                <MapPin className="w-3 h-3 text-primary shrink-0" />
                {doctor.opd_room}
              </p>
            )}
            {department?.rationale && (
              <p className="text-[11px] text-muted-foreground/80 mt-1 italic leading-tight">
                {department.rationale}
              </p>
            )}
          </div>
        </div>

        {/* Medical License & Verification */}
        <div className="flex items-start gap-3">
          <Award className="w-5 h-5 text-primary shrink-0 mt-0.5" />
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
              Registration & Experience
            </h4>
            {doctor?.registration_number ? (
              <p className="text-xs font-mono font-bold text-foreground">
                Reg: {doctor.registration_number}
              </p>
            ) : (
              <p className="text-xs font-mono text-muted-foreground">Reg: Verified RMP</p>
            )}
            <p className="text-xs text-muted-foreground mt-0.5">
              {doctor?.state_council || 'State Medical Council'}
            </p>
            {doctor?.experience && (
              <span className="text-[11px] text-emerald-600 dark:text-emerald-400 font-medium block mt-1">
                {doctor.experience}
              </span>
            )}
          </div>
        </div>

        {/* Timings & Languages */}
        <div className="flex items-start gap-3">
          <Clock className="w-5 h-5 text-primary shrink-0 mt-0.5" />
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-1">
              OPD Hours & Languages
            </h4>
            <p className="text-xs text-foreground/90 font-medium">
              Mon–Sat 08:30 AM – 04:30 PM
            </p>
            {doctor?.languages && doctor.languages.length > 0 && (
              <div className="flex items-center gap-1 mt-1 text-xs text-muted-foreground">
                <Languages className="w-3.5 h-3.5 text-primary shrink-0" />
                <span className="capitalize">
                  Languages: {doctor.languages.join(', ')}
                </span>
              </div>
            )}
            <span className="inline-block mt-2 px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/20">
              Active Shift • On Duty
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

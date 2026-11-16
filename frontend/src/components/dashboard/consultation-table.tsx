'use client';
import { formatDistanceToNow } from 'date-fns';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { PriorityBadge } from '@/components/results/priority-badge';

export function ConsultationTable({ consultations, onSelect }: { consultations: any[], onSelect: (c: any) => void }) {
  if (consultations.length === 0) {
    return (
      <div className="bg-card border border-border rounded-xl p-12 text-center text-muted-foreground shadow-sm">
        No consultations found matching your criteria.
      </div>
    );
  }

  return (
    <div className="bg-card border border-border rounded-xl shadow-sm overflow-hidden animate-gentle-fade-in">
      <Table>
        <TableHeader className="bg-muted/30">
          <TableRow className="border-border">
            <TableHead className="w-[130px]">Time</TableHead>
            <TableHead>Patient</TableHead>
            <TableHead>Language</TableHead>
            <TableHead className="hidden md:table-cell">Chief Complaint</TableHead>
            <TableHead>Priority</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {consultations.map((c) => (
            <TableRow 
              key={c.id} 
              className="cursor-pointer hover:bg-muted/30 transition-colors border-border"
              onClick={() => onSelect(c)}
            >
              <TableCell className="text-muted-foreground whitespace-nowrap text-xs">
                {c.created_at ? formatDistanceToNow(new Date(c.created_at), { addSuffix: true }) : 'Unknown'}
              </TableCell>
              <TableCell>
                <div className="font-semibold text-foreground text-xs leading-tight">
                  {c.patient_name || c.guest_name || c.result?.clinical_summary?.stated_patient_name || 'Patient'}
                </div>
                {c.phone_number && (
                  <div className="text-[10px] text-muted-foreground">{c.phone_number}</div>
                )}
              </TableCell>
              <TableCell className="font-medium text-xs">
                {c.result?.patient_input?.language || c.patient_language || 'Unknown'}
              </TableCell>
              <TableCell className="hidden md:table-cell text-muted-foreground truncate max-w-[260px] text-xs">
                {c.chief_complaint || c.result?.clinical_summary?.chief_complaint || 'N/A'}
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-1.5 flex-wrap">
                  {c.result?.priority?.level ? (
                    <PriorityBadge level={c.result.priority.level} size="sm" />
                  ) : (
                    <span className="text-muted-foreground">Unknown</span>
                  )}
                  {c.result?.priority?.urgency_tier && (
                    <span className={`px-2 py-0.5 rounded-full font-bold uppercase text-[9px] ${
                      c.result.priority.urgency_tier === 'emergency'
                        ? 'bg-destructive text-white'
                        : c.result.priority.urgency_tier === 'doctor_today'
                        ? 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/25'
                        : c.result.priority.urgency_tier === 'doctor_soon'
                        ? 'bg-blue-500/15 text-blue-700 dark:text-blue-400 border border-blue-500/25'
                        : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/25'
                    }`}>
                      {c.result.priority.urgency_tier.replace('_', ' ')}
                    </span>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

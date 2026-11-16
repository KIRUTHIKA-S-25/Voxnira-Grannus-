'use client';
import { useEffect, useState, Suspense } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { PipelineResult, getConsultationDetails } from '@/lib/api';
import { PriorityBadge } from '@/components/results/priority-badge';
import { SymptomCard } from '@/components/results/symptom-card';
import { SafetyFlags } from '@/components/results/safety-flags';
import { TranscriptView } from '@/components/results/transcript-view';
import { TranslationCard } from '@/components/results/translation-card';
import { HomeRemedy } from '@/components/results/home-remedy';
import { AcousticBiomarkerCard } from '@/components/results/acoustic-biomarker-card';
import { EmergencyScreen } from '@/components/results/emergency-screen';
import { FollowUpCard } from '@/components/results/follow-up-card';
import { HospitalVisitCard } from '@/components/results/hospital-visit-card';
import { VoiceThreadViewer } from '@/components/results/voice-thread-viewer';
import { FhirExportButton } from '@/components/dashboard/fhir-export-button';
import { VoiceReplyRecorder } from '@/components/dashboard/voice-reply-recorder';
import { Button } from '@/components/ui/button';
import { ArrowLeft, Plus, Pill, AlertCircle } from 'lucide-react';
import { motion } from 'framer-motion';

function ResultsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = searchParams?.get('id');
  
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [consultationMeta, setConsultationMeta] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [isDoctor, setIsDoctor] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem('grannus_user');
      if (stored) {
        const u = JSON.parse(stored);
        if (['doctor', 'admin'].includes(u.role)) {
          setIsDoctor(true);
        }
      }
    } catch {
      setIsDoctor(false);
    }
  }, []);

  useEffect(() => {
    if (!id) {
      router.push('/input');
      return;
    }

    const fetchResult = async () => {
      try {
        const data = await getConsultationDetails(id);
        setConsultationMeta(data);
        if (data && data.full_result) {
          setResult(data.full_result);
        } else if (data) {
          setResult(data);
        }
      } catch (err) {
        console.error('Error fetching result:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchResult();
  }, [id, router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="animate-pulse flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-primary/30 border-t-primary rounded-full animate-spin" />
          <p className="text-muted-foreground">Loading results...</p>
        </div>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 text-center px-4">
        <h2 className="text-2xl font-heading font-medium">Result not found</h2>
        <p className="text-muted-foreground">The consultation you're looking for doesn't exist or you don't have access.</p>
        <Button onClick={() => router.push('/input')}>Go to Voice Input</Button>
      </div>
    );
  }

  const { patient_input, clinical_summary, safety_screening, priority, doctor_translated_summary, home_remedy_guidance, acoustic_biomarkers } = result;
  const hasCriticalFlags = safety_screening.red_flags.some((f: any) => f.severity === 'critical');

  return (
    <div className="pb-24 animate-gentle-fade-in">
      {/* Priority Banner */}
      <div className="w-full bg-card border-b border-border shadow-sm py-8 px-4 mb-8">
        <div className="container mx-auto max-w-5xl flex flex-col md:flex-row items-start md:items-center justify-between gap-6">
          <div>
            <h1 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-2">Priority Assessment</h1>
            <PriorityBadge
              level={priority.level}
              confidence={priority.confidence}
              size="lg"
              showConfidence={priority.model_used !== 'fail_to_review'}
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {priority.reasons
                .filter(
                  (r: string) =>
                    !['age_group', 'severity_unknown', 'duration_days', 'symptom_count', 'high_risk_symptom_count', 'pain_score', 'fever_score'].includes(r) &&
                    !r.startsWith('Model:')
                )
                .map((r: string, i: number) => (
                  <span key={i} className="text-sm text-foreground bg-secondary/50 px-3 py-1 rounded-full">
                    {r}
                  </span>
                ))}
            </div>
          </div>
          
          <div className="flex flex-wrap items-center gap-3 w-full md:w-auto">
            {isDoctor && (
              <div className="w-full sm:w-auto">
                <FhirExportButton result={result} />
              </div>
            )}
            <Button
              variant="outline"
              className="flex-1 md:flex-none gap-2 rounded-full border-border bg-background"
              onClick={() => router.push(isDoctor ? '/dashboard' : '/history')}
            >
              <ArrowLeft className="w-4 h-4" /> {isDoctor ? 'Dashboard' : 'History'}
            </Button>
            <Button className="flex-1 md:flex-none gap-2 rounded-full bg-primary" onClick={() => router.push('/input')}>
              <Plus className="w-4 h-4" /> New
            </Button>
          </div>
        </div>
      </div>

      <div className="container mx-auto max-w-5xl px-4 space-y-10">
        {/* Patient-friendly alert when AI extraction was incomplete (B7) */}
        {priority.model_used === 'fail_to_review' && (
          <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3 text-amber-900 dark:text-amber-200">
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
            <div>
              <p className="font-semibold text-sm">We couldn't clearly understand your voice recording.</p>
              <p className="text-xs text-muted-foreground mt-0.5">Please try recording again with less background noise, or a duty doctor will review your case directly today.</p>
            </div>
          </div>
        )}

        {/* Prominent Emergency Screen for Critical Cases */}
        {(priority.urgency_tier === 'emergency' || (priority.level === 'HIGH' && (priority.emergency_override || hasCriticalFlags))) && (
          <motion.section initial={{ scale: 0.96, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ duration: 0.3 }}>
            <EmergencyScreen
              reasons={priority.reasons}
              emergencyNumbers={priority.emergency_call_numbers || ['108', '112']}
            />
          </motion.section>
        )}

        {/* Doctor Summary / Home Remedies depending on priority */}
        {['HIGH', 'MEDIUM', 'PENDING_REVIEW'].includes(priority.level) && doctor_translated_summary && (
          <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.1 }}>
            <TranslationCard data={doctor_translated_summary} />
          </motion.section>
        )}

        {priority.level === 'LOW' && home_remedy_guidance && (
          <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.1 }}>
            <HomeRemedy data={home_remedy_guidance} />
          </motion.section>
        )}

        {/* Official Doctor Prescription Card (Telemedicine Guidelines 2020) */}
        {consultationMeta?.prescriptions && consultationMeta.prescriptions.length > 0 && (
          <motion.section
            initial={{ y: 20, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ delay: 0.11 }}
            className="p-6 bg-emerald-500/10 border-2 border-emerald-500/30 rounded-2xl shadow-sm space-y-4"
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between pb-3 border-b border-emerald-500/20 gap-2">
              <div className="flex items-center gap-2">
                <div className="w-9 h-9 rounded-full bg-emerald-600 text-white flex items-center justify-center font-bold text-sm">
                  <Pill className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-foreground">Official Clinical Prescription</h3>
                  <p className="text-xs text-muted-foreground">Authorized by attending Registered Medical Practitioner (RMP)</p>
                </div>
              </div>
              <div className="flex items-center gap-2 text-xs">
                {consultationMeta.doctor_registration_number && (
                  <span className="bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 font-mono px-3 py-1 rounded-full font-bold border border-emerald-500/30">
                    NMC / Council: {consultationMeta.doctor_registration_number}
                  </span>
                )}
                {consultationMeta.prescribed_by && (
                  <span className="text-muted-foreground">
                    Dr. {consultationMeta.prescribed_by}
                  </span>
                )}
              </div>
            </div>

            <div className="divide-y divide-emerald-500/15">
              {consultationMeta.prescriptions.map((rx: any, idx: number) => (
                <div key={idx} className="py-3 first:pt-0 last:pb-0 flex flex-col md:flex-row md:items-center justify-between gap-2">
                  <div className="space-y-0.5">
                    <span className="font-bold text-foreground text-sm flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-emerald-500" />
                      {rx.medication_name}
                      <span className="text-xs font-normal text-muted-foreground">({rx.dosage})</span>
                    </span>
                    {rx.instructions && (
                      <p className="text-xs text-muted-foreground italic pl-4">
                        Instructions: {rx.instructions}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs font-mono text-muted-foreground pl-4 md:pl-0">
                    <span className="bg-background px-2.5 py-1 rounded-lg border border-border">
                      {rx.frequency}
                    </span>
                    <span className="bg-background px-2.5 py-1 rounded-lg border border-border">
                      {rx.duration}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <div className="text-[11px] text-muted-foreground flex items-center justify-between pt-2 border-t border-emerald-500/15">
              <span>Under India Telemedicine Practice Guidelines 2020. Valid for authorized pharmacy dispensing.</span>
              {consultationMeta.prescription_timestamp && (
                <span className="font-mono">
                  {new Date(consultationMeta.prescription_timestamp * 1000).toLocaleDateString()}
                </span>
              )}
            </div>
          </motion.section>
        )}

        {/* 2 to 3 Day Follow-Up & One-Tap Clinical Escalation */}
        <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.12 }}>
          <FollowUpCard
            consultationId={id || result.request_id}
            initialTier={priority.urgency_tier || (priority.level === 'LOW' ? 'self_care' : 'doctor_soon')}
            followUpDays={priority.follow_up_days || 2}
          />
        </motion.section>

        {/* Asynchronous Two-Way Voice Thread (F2.8) */}
        <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.13 }}>
          <VoiceThreadViewer
            consultationId={id || result.request_id}
            patientLanguage={patient_input.language || 'ta-IN'}
          />
        </motion.section>

        {/* Official Hospital Visit Card (F2.9) */}
        <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.14 }}>
          <HospitalVisitCard />
        </motion.section>

        {/* Doctor Voice Reply to Patient */}
        <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.15 }}>
          <VoiceReplyRecorder 
            patientLanguage={patient_input.language || 'hi-IN'} 
            consultationId={id || undefined} 
          />
        </motion.section>

        {/* Acoustic Biomarkers */}
        {acoustic_biomarkers && (
          <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.15 }}>
            <AcousticBiomarkerCard data={acoustic_biomarkers} />
          </motion.section>
        )}

        {/* Safety Flags */}
        {safety_screening.red_flags && safety_screening.red_flags.length > 0 && (
          <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.2 }}>
            <SafetyFlags flags={safety_screening.red_flags as any} hasCritical={hasCriticalFlags} />
          </motion.section>
        )}

        {/* Transcript */}
        <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.3 }}>
          <TranscriptView 
            original={patient_input.transcript_original} 
            english={patient_input.transcript_english} 
            language={patient_input.language} 
          />
        </motion.section>

        {/* Clinical Summary */}
        <motion.section initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} transition={{ delay: 0.4 }} className="space-y-6">
          <div>
            <h2 className="text-2xl font-heading font-medium text-foreground mb-6">Clinical Extraction</h2>
            
            <div className="bg-card p-6 rounded-xl border border-border shadow-sm mb-6">
              <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-2">Chief Complaint</h3>
              <p className="text-xl text-foreground font-medium">{clinical_summary.chief_complaint || 'Not specified'}</p>
            </div>
          </div>

          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-4">Identified Symptoms</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {clinical_summary.symptoms && clinical_summary.symptoms.length > 0 ? (
                clinical_summary.symptoms.map((s, i) => (
                  <SymptomCard key={i} symptom={s as any} />
                ))
              ) : (
                <div className="col-span-full p-6 text-center border border-dashed border-border rounded-xl text-muted-foreground">
                  No symptoms identified in the audio.
                </div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-4">
            <div className="bg-muted/20 p-5 rounded-xl border border-border">
              <h4 className="font-medium mb-3 flex items-center gap-2">Conditions</h4>
              <ul className="space-y-2">
                {clinical_summary.existing_conditions?.length ? clinical_summary.existing_conditions.map((c, i) => (
                  <li key={i} className="text-sm text-foreground flex gap-2"><span className="text-primary">•</span>{c}</li>
                )) : <li className="text-sm text-muted-foreground italic">None reported</li>}
              </ul>
            </div>
            
            <div className="bg-muted/20 p-5 rounded-xl border border-border">
              <h4 className="font-medium mb-3 flex items-center gap-2">Medications</h4>
              <ul className="space-y-2">
                {clinical_summary.medications?.length ? clinical_summary.medications.map((m, i) => (
                  <li key={i} className="text-sm text-foreground flex gap-2"><span className="text-primary">•</span>{m}</li>
                )) : <li className="text-sm text-muted-foreground italic">None reported</li>}
              </ul>
            </div>
            
            <div className="bg-muted/20 p-5 rounded-xl border border-border">
              <h4 className="font-medium mb-3 flex items-center gap-2">Allergies</h4>
              <ul className="space-y-2">
                {clinical_summary.allergies?.length ? clinical_summary.allergies.map((a, i) => (
                  <li key={i} className="text-sm text-foreground flex gap-2"><span className="text-primary">•</span>{a}</li>
                )) : <li className="text-sm text-muted-foreground italic">None reported</li>}
              </ul>
            </div>
          </div>
        </motion.section>
      </div>
    </div>
  );
}

export default function ResultsPage() {
  return (
    <Suspense fallback={<div className="flex items-center justify-center min-h-[60vh]"><div className="animate-pulse w-12 h-12 border-4 border-primary/30 border-t-primary rounded-full animate-spin" /></div>}>
      <ResultsContent />
    </Suspense>
  );
}

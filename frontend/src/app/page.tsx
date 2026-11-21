'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { motion, AnimatePresence } from 'framer-motion';
import { Button } from '@/components/ui/button';
import {
  Mic,
  Activity,
  Shield,
  Brain,
  ArrowRight,
  Radio,
  Stethoscope,
  Globe,
  CheckCircle2,
  Clock,
  HeartPulse,
  Building2,
  Volume2,
  UserCheck,
} from 'lucide-react';
import { SUPPORTED_LANGUAGES } from '@/lib/api';

interface DemoCase {
  id: string;
  langName: string;
  langCode: string;
  nativeSample: string;
  englishTranslation: string;
  chiefComplaint: string;
  symptoms: string[];
  urgencyTier: 'emergency' | 'doctor_today' | 'doctor_soon' | 'self_care';
  urgencyLabel: string;
  specialist: string;
  hospital: string;
  confidence: number;
}

const DEMO_CASES: DemoCase[] = [
  {
    id: 'te',
    langName: 'Telugu',
    langCode: 'te-IN',
    nativeSample: 'నాకు రెండు రోజులుగా ఛాతీలో విపరీతమైన మంట, ఎడమ చేతికి నొప్పి పాకుతోంది, శ్వాస తీసుకోవడం కష్టంగా ఉంది.',
    englishTranslation: 'Severe burning chest pain radiating to left arm with acute shortness of breath for 2 days.',
    chiefComplaint: 'Acute chest discomfort with left arm radiation',
    symptoms: ['Chest Pain', 'Left Arm Radiating Pain', 'Dyspnea', 'Diaphoresis'],
    urgencyTier: 'emergency',
    urgencyLabel: 'Emergency — Level 1 Immediate',
    specialist: 'Cardiologist / Emergency Care',
    hospital: 'Tirupati District General Hospital',
    confidence: 96,
  },
  {
    id: 'hi',
    langName: 'Hindi',
    langCode: 'hi-IN',
    nativeSample: 'तीन दिन से लगातार तेज़ बुखार है, सिर में भारी दर्द है और आँखों के पीछे बहुत खिंचाव लग रहा है।',
    englishTranslation: 'High continuous fever for 3 days with severe retro-orbital headache and muscle weakness.',
    chiefComplaint: 'High continuous pyrexia with retro-orbital pain',
    symptoms: ['High Grade Fever', 'Retro-orbital Headache', 'Body Ache', 'Chills'],
    urgencyTier: 'doctor_today',
    urgencyLabel: 'Doctor Today — High Priority',
    specialist: 'General Physician / Internal Medicine',
    hospital: 'Rampur Community Health Centre',
    confidence: 92,
  },
  {
    id: 'ta',
    langName: 'Tamil',
    langCode: 'ta-IN',
    nativeSample: 'ஒரு வாரமாக விடாத வறட்டு இருமல் இருக்கிறது, இரவில் மூச்சுத்திணறல் அதிகமாகிறது.',
    englishTranslation: 'Persistent dry cough for over a week, worsening nocturnal breathlessness.',
    chiefComplaint: 'Subacute nocturnal cough with mild dyspnea',
    symptoms: ['Dry Cough > 7 Days', 'Nocturnal Dyspnea', 'Wheezing'],
    urgencyTier: 'doctor_soon',
    urgencyLabel: 'Doctor Soon — Routine Triage',
    specialist: 'Pulmonologist',
    hospital: 'Thanjavur Medical College Hospital',
    confidence: 89,
  },
  {
    id: 'bn',
    langName: 'Bengali',
    langCode: 'bn-IN',
    nativeSample: 'সকাল থেকে পেটের ডান পাশে তীব্র যন্ত্রণা শুরু হয়েছে এবং বমি বমি ভাব হচ্ছে।',
    englishTranslation: 'Sudden sharp pain in right lower abdomen starting this morning accompanied by nausea.',
    chiefComplaint: 'Acute right lower quadrant abdominal pain',
    symptoms: ['Right Iliac Fossa Pain', 'Nausea', 'Loss of Appetite'],
    urgencyTier: 'doctor_today',
    urgencyLabel: 'Doctor Today — Surgical Review',
    specialist: 'General Surgery / Gastroenterology',
    hospital: 'Burdwan District Hospital',
    confidence: 94,
  },
];

export default function LandingPage() {
  const router = useRouter();
  const [selectedDemo, setSelectedDemo] = useState<DemoCase>(DEMO_CASES[0]);
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);

  const features = [
    {
      title: 'Acoustic & Voice AI',
      desc: 'Native recognition for 11 regional languages and dialectal variations without requiring complex typing or smartphone literacy.',
      icon: Mic,
      tag: 'Indic Whisper STT',
    },
    {
      title: 'Multi-Factor Clinical Triage',
      desc: 'Extracts clinical entities, analyzes red-flag symptoms, and assigns precise priority using validated emergency triage protocols.',
      icon: Brain,
      tag: 'Clinical Decision Engine',
    },
    {
      title: 'Direct Hospital Escalation',
      desc: 'Seamlessly dispatches structured EHR summaries and audio logs directly to on-duty government hospital specialists.',
      icon: Stethoscope,
      tag: 'Doctor Workstation',
    },
    {
      title: 'Safety Guardrails & SOS',
      desc: 'Instant red-flag detection triggers emergency siren alerts, nearby ambulance dispatch coordinates, and zero-delay guidance.',
      icon: Shield,
      tag: 'Protocol Aligned',
    },
  ];

  const handleSimulatePlay = () => {
    setIsPlayingAudio(true);
    setTimeout(() => setIsPlayingAudio(false), 3500);
  };

  return (
    <div className="w-full relative overflow-hidden bg-background">
      {/* Ambient background glows */}
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-full max-w-7xl h-[600px] bg-radial-glow pointer-events-none -z-10" />
      <div className="absolute top-48 left-10 w-96 h-96 bg-primary/8 rounded-full blur-[100px] pointer-events-none -z-10 animate-float-slow" />
      <div className="absolute top-80 right-10 w-96 h-96 bg-emerald-500/8 rounded-full blur-[120px] pointer-events-none -z-10 animate-float-slow" style={{ animationDelay: '3s' }} />

      {/* Hero Section */}
      <section className="relative pt-12 pb-20 lg:pt-20 lg:pb-28 px-4 sm:px-6">
        <div className="container mx-auto max-w-6xl">
          {/* Header Title & Tagline */}
          <div className="text-center max-w-3xl mx-auto mb-14">
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
              className="inline-flex items-center gap-2.5 px-4 py-1.5 rounded-full bg-primary/10 border border-primary/25 text-primary text-xs font-semibold tracking-wide uppercase mb-6 shadow-xs"
            >
              <span className="w-2 h-2 rounded-full bg-primary animate-pulse-glow" />
              <span>Rural Tele-Triage & Hospital Dispatch</span>
              <span className="text-primary/40">•</span>
              <span className="text-foreground/80 font-medium">11 Indic Languages</span>
            </motion.div>

            <motion.h1
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.1 }}
              className="text-4xl sm:text-5xl lg:text-6xl font-heading font-bold text-foreground tracking-tight leading-[1.15] mb-6"
            >
              Intelligent Voice Triage for{' '}
              <span className="text-primary underline decoration-primary/30 decoration-wavy decoration-2 underline-offset-8">
                Rural Primary Health
              </span>
            </motion.h1>

            <motion.p
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.2 }}
              className="text-base sm:text-lg lg:text-xl text-muted-foreground leading-relaxed font-normal"
            >
              Bridging the medical specialist gap for rural patients. Speak naturally in your native language—our clinical AI extracts symptoms, calculates clinical urgency, and connects you directly with registered hospital doctors.
            </motion.p>

            {/* CTAs */}
            <motion.div
              initial={{ opacity: 0, y: 15 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 0.3 }}
              className="flex flex-col sm:flex-row items-center justify-center gap-4 mt-8"
            >
              <Button
                size="lg"
                onClick={() => router.push('/input')}
                className="w-full sm:w-auto h-14 px-8 rounded-full text-base font-semibold shadow-lg hover:shadow-primary/25 bg-primary hover:bg-primary/95 text-primary-foreground gap-3 transition-all duration-300 hover:scale-[1.02] active:scale-[0.98]"
              >
                <div className="w-8 h-8 rounded-full bg-primary-foreground/15 flex items-center justify-center">
                  <Mic className="w-4 h-4 text-primary-foreground" />
                </div>
                <span>Start Voice Consultation</span>
                <ArrowRight className="w-4 h-4 ml-1" />
              </Button>

              <Button
                size="lg"
                variant="outline"
                onClick={() => router.push('/dashboard')}
                className="w-full sm:w-auto h-14 px-8 rounded-full text-base font-medium glass-card hover:bg-card/90 border-border/80 gap-3 transition-all duration-300 hover:scale-[1.02] active:scale-[0.98]"
              >
                <Stethoscope className="w-4 h-4 text-primary" />
                <span>Doctor Workstation</span>
              </Button>
            </motion.div>

            {/* Micro Trust Pills */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.8, delay: 0.4 }}
              className="flex flex-wrap items-center justify-center gap-6 mt-8 text-xs text-muted-foreground"
            >
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-primary" />
                <span>Zero App Download Needed</span>
              </div>
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-primary" />
                <span>Safe 4-Digit PIN Access</span>
              </div>
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 text-primary" />
                <span>Licensed Doctor-in-the-Loop</span>
              </div>
            </motion.div>
          </div>

          {/* Interactive Live Triage Simulation Card (Hero Deck) */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.35 }}
            className="w-full max-w-4xl mx-auto rounded-3xl glass-card border border-border/70 p-5 sm:p-7 shadow-xl shadow-black/5 relative overflow-hidden"
          >
            {/* Top Bar with Live Badge & Language Switcher */}
            <div className="flex flex-wrap items-center justify-between gap-4 pb-5 border-b border-border/50">
              <div className="flex items-center gap-3">
                <div className="w-3 h-3 rounded-full bg-emerald-500 animate-pulse-glow" />
                <div>
                  <h3 className="font-heading font-semibold text-sm text-foreground flex items-center gap-2">
                    Live Telemetry Preview
                    <span className="text-[11px] font-mono font-normal text-muted-foreground px-2 py-0.5 rounded-md bg-muted/60">
                      STT + Clinical Triage Pipeline
                    </span>
                  </h3>
                </div>
              </div>

              {/* Language Pills */}
              <div className="flex items-center gap-1.5 bg-muted/50 p-1 rounded-full border border-border/60">
                {DEMO_CASES.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => {
                      setSelectedDemo(item);
                      setIsPlayingAudio(false);
                    }}
                    className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                      selectedDemo.id === item.id
                        ? 'bg-primary text-primary-foreground shadow-xs'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    {item.langName}
                  </button>
                ))}
              </div>
            </div>

            {/* Simulated Live Processing Deck */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 mt-6 items-stretch">
              {/* Left Column: Voice Audio & Speech Transcription (5 cols) */}
              <div className="lg:col-span-5 flex flex-col justify-between space-y-4 bg-muted/30 p-5 rounded-2xl border border-border/60">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <span className="text-xs font-semibold tracking-wider text-muted-foreground uppercase flex items-center gap-1.5">
                      <Radio className="w-3.5 h-3.5 text-primary" />
                      Patient Voice Input
                    </span>
                    <button
                      onClick={handleSimulatePlay}
                      className="inline-flex items-center gap-1.5 text-xs text-primary font-medium hover:underline"
                    >
                      <Volume2 className="w-3.5 h-3.5" />
                      {isPlayingAudio ? 'Listening...' : 'Simulate Audio'}
                    </button>
                  </div>

                  {/* Audio Waveform Simulation */}
                  <div className="h-14 bg-background/80 rounded-xl border border-border/60 flex items-center justify-center px-4 gap-1.5 overflow-hidden">
                    {[18, 32, 22, 45, 28, 55, 38, 48, 20, 60, 42, 30, 52, 25, 40, 20, 35, 50, 28, 15].map((h, idx) => (
                      <span
                        key={idx}
                        className={`w-1 rounded-full bg-primary/70 transition-all duration-300 ${
                          isPlayingAudio ? 'animate-pulse' : ''
                        }`}
                        style={{
                          height: isPlayingAudio ? `${Math.max(12, (h * ((idx % 3) + 1.2)) % 44)}px` : `${Math.max(10, h * 0.5)}px`,
                        }}
                      />
                    ))}
                  </div>

                  {/* Native Audio Transcript */}
                  <div className="mt-4">
                    <p className="text-xs text-muted-foreground font-medium mb-1">Original Audio Transcript ({selectedDemo.langName}):</p>
                    <p className="text-sm text-foreground/90 font-medium italic bg-card/60 p-3 rounded-lg border border-border/40">
                      &ldquo;{selectedDemo.nativeSample}&rdquo;
                    </p>
                  </div>
                </div>

                {/* English Normalized Translation */}
                <div className="pt-2 border-t border-border/40">
                  <p className="text-xs text-muted-foreground font-medium mb-1">Clinical Translation (English):</p>
                  <p className="text-xs text-foreground/80 leading-relaxed font-sans">
                    {selectedDemo.englishTranslation}
                  </p>
                </div>
              </div>

              {/* Right Column: AI Triage & Specialist Routing (7 cols) */}
              <div className="lg:col-span-7 flex flex-col justify-between space-y-4 bg-muted/20 p-5 rounded-2xl border border-border/60">
                <AnimatePresence mode="wait">
                  <motion.div
                    key={selectedDemo.id}
                    initial={{ opacity: 0, x: 10 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -10 }}
                    transition={{ duration: 0.3 }}
                    className="space-y-4"
                  >
                    {/* Urgency Badge & Confidence */}
                    <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-border/40">
                      <div>
                        <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase block mb-1">
                          Calculated Urgency Tier
                        </span>
                        <div
                          className={`inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-semibold ${
                            selectedDemo.urgencyTier === 'emergency'
                              ? 'bg-red-500/15 text-red-700 dark:text-red-400 border border-red-500/30'
                              : selectedDemo.urgencyTier === 'doctor_today'
                              ? 'bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-500/30'
                              : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border border-emerald-500/30'
                          }`}
                        >
                          <HeartPulse className="w-3.5 h-3.5" />
                          <span>{selectedDemo.urgencyLabel}</span>
                        </div>
                      </div>

                      <div className="text-right">
                        <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase block mb-1">
                          Model Certainty
                        </span>
                        <span className="text-xs font-mono font-bold text-foreground">
                          {selectedDemo.confidence}% Confidence
                        </span>
                      </div>
                    </div>

                    {/* Chief Complaint & Extracted Entities */}
                    <div>
                      <span className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase block mb-1">
                        Synthesized Chief Complaint
                      </span>
                      <p className="text-sm font-semibold text-foreground">
                        {selectedDemo.chiefComplaint}
                      </p>
                      
                      <div className="flex flex-wrap gap-1.5 mt-2.5">
                        {selectedDemo.symptoms.map((s, idx) => (
                          <span
                            key={idx}
                            className="text-xs px-2.5 py-1 rounded-md bg-card border border-border/80 text-foreground/80 font-medium"
                          >
                            • {s}
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* Doctor Routing Dispatch Card */}
                    <div className="p-3.5 rounded-xl bg-card border border-border/80 flex items-center justify-between gap-3 shadow-xs">
                      <div className="flex items-center gap-3">
                        <div className="w-9 h-9 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
                          <Building2 className="w-4 h-4" />
                        </div>
                        <div>
                          <p className="text-xs font-semibold text-foreground">
                            {selectedDemo.specialist}
                          </p>
                          <p className="text-[11px] text-muted-foreground">
                            Routed to: {selectedDemo.hospital}
                          </p>
                        </div>
                      </div>
                      <div className="text-right shrink-0">
                        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded-full">
                          <Clock className="w-3 h-3" /> Assigned
                        </span>
                      </div>
                    </div>
                  </motion.div>
                </AnimatePresence>
              </div>
            </div>
          </motion.div>
        </div>
      </section>

      {/* Core Architectural Pillars (Bento Grid) */}
      <section className="py-20 bg-muted/20 border-y border-border/60 px-4 sm:px-6 relative">
        <div className="container mx-auto max-w-6xl">
          <div className="text-center max-w-2xl mx-auto mb-14">
            <h2 className="text-3xl sm:text-4xl font-heading font-bold text-foreground tracking-tight mb-4">
              Engineered for Clinical Precision
            </h2>
            <p className="text-muted-foreground text-base leading-relaxed">
              Every step of the voice triage pipeline is designed to assist rural health workers, eliminate diagnostic delays, and safeguard patient safety.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {features.map((f, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 20 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.1, duration: 0.5 }}
                className="glass-card glass-card-hover p-6 rounded-2xl flex flex-col justify-between"
              >
                <div>
                  <div className="w-12 h-12 bg-primary/10 text-primary rounded-xl flex items-center justify-center mb-5">
                    <f.icon className="w-6 h-6" />
                  </div>
                  <span className="text-[10px] font-mono uppercase tracking-wider text-primary font-semibold block mb-2">
                    {f.tag}
                  </span>
                  <h3 className="text-lg font-heading font-semibold text-foreground mb-2">
                    {f.title}
                  </h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {f.desc}
                  </p>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Multilingual Support Grid */}
      <section className="py-20 px-4 sm:px-6">
        <div className="container mx-auto max-w-5xl text-center">
          <div className="inline-flex items-center gap-2 px-3.5 py-1 rounded-full bg-muted border border-border text-xs font-medium text-muted-foreground mb-4">
            <Globe className="w-3.5 h-3.5 text-primary" />
            <span>Inclusive Healthcare Coverage</span>
          </div>
          <h2 className="text-3xl sm:text-4xl font-heading font-bold text-foreground tracking-tight mb-4">
            11 Indic Languages Supported
          </h2>
          <p className="text-muted-foreground max-w-xl mx-auto mb-12 text-sm sm:text-base">
            Patients can articulate their symptoms freely without fear of language or literacy barriers.
          </p>

          <div className="flex flex-wrap justify-center gap-3 max-w-4xl mx-auto">
            {SUPPORTED_LANGUAGES.filter((l) => l.code !== 'unknown' && l.code !== 'en-IN').map((lang, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, scale: 0.95 }}
                whileInView={{ opacity: 1, scale: 1 }}
                viewport={{ once: true }}
                transition={{ delay: i * 0.04 }}
                className="px-5 py-2.5 glass-card rounded-full text-foreground hover:border-primary/50 transition-all cursor-default flex items-center gap-2.5 shadow-xs hover:shadow-sm hover:scale-105"
              >
                <span className="font-semibold text-sm">{lang.name}</span>
                <span className="text-muted-foreground/40 text-xs">|</span>
                <span className="text-muted-foreground text-xs font-medium">{lang.nativeName}</span>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* Bottom CTA Banner */}
      <section className="py-16 px-4 sm:px-6 border-t border-border/60 bg-muted/30">
        <div className="container mx-auto max-w-4xl text-center">
          <div className="glass-card p-8 sm:p-12 rounded-3xl border border-border/80 relative overflow-hidden">
            <div className="relative z-10">
              <h2 className="text-2xl sm:text-3xl font-heading font-bold text-foreground mb-4">
                Ready for Voice-First Primary Triage?
              </h2>
              <p className="text-muted-foreground text-sm sm:text-base max-w-xl mx-auto mb-8">
                Initiate a voice consultation now. Secure, swift, and routed to verified healthcare providers.
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
                <Button
                  size="lg"
                  onClick={() => router.push('/input')}
                  className="w-full sm:w-auto h-12 px-8 rounded-full shadow-md bg-primary hover:bg-primary/95 text-primary-foreground gap-2 font-medium"
                >
                  <Mic className="w-4 h-4" /> Start Voice Consultation
                </Button>
                <Button
                  size="lg"
                  variant="outline"
                  onClick={() => router.push('/login')}
                  className="w-full sm:w-auto h-12 px-8 rounded-full glass-card hover:bg-card gap-2 font-medium"
                >
                  <UserCheck className="w-4 h-4" /> Patient / Clinician Sign In
                </Button>
              </div>
            </div>
            {/* Background glow behind CTA */}
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-80 h-80 bg-primary/10 rounded-full blur-3xl -z-0 pointer-events-none" />
          </div>
        </div>
      </section>
    </div>
  );
}
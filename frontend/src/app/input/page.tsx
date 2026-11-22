'use client';
import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { AudioRecorder } from '@/components/voice/audio-recorder';
import { FileUploader } from '@/components/voice/file-uploader';
import { SUPPORTED_LANGUAGES, getActiveProfile, PatientProfile, recordPatientConsent, triggerEmergencySOS } from '@/lib/api';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronDown, ChevronUp, ShieldCheck, PhoneCall, MapPin, AlertCircle } from 'lucide-react';
import { ConsentDialog } from '@/components/voice/consent-dialog';
import { setPipelinePayload } from '@/lib/pipeline-store';

export default function VoiceInputPage() {
  const router = useRouter();
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [language, setLanguage] = useState('unknown');
  const [doctorLanguage, setDoctorLanguage] = useState('en-IN');
  const [activeProfile, setActiveProfileState] = useState<PatientProfile | null>(null);
  
  // DPDP Consent state
  const [showConsentModal, setShowConsentModal] = useState(true);
  const [consentApproved, setConsentApproved] = useState(false);
  const [consentId, setConsentId] = useState<string | null>(null);
  const [isSavingConsent, setIsSavingConsent] = useState(false);
  const [consentError, setConsentError] = useState<string | null>(null);

  // Optional context
  const [showContext, setShowContext] = useState(false);
  const [age, setAge] = useState('');
  const [gender, setGender] = useState('');
  const [duration, setDuration] = useState('');
  const [conditions, setConditions] = useState('');
  const [medications, setMedications] = useState('');

  // Geolocation state
  const [locationCoords, setLocationCoords] = useState<{ latitude: number; longitude: number } | null>(null);
  const [locationError, setLocationError] = useState<string | null>(null);
  const [isLocating, setIsLocating] = useState(false);

  const requestLocation = () => {
    if (typeof window === 'undefined' || !navigator.geolocation) {
      setLocationError('Geolocation is not supported by your browser.');
      return;
    }
    setIsLocating(true);
    setLocationError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocationCoords({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
        });
        setIsLocating(false);
      },
      (err) => {
        setIsLocating(false);
        setLocationError('Location access denied or unavailable. Consultation will proceed without GPS.');
      },
      { enableHighAccuracy: false, timeout: 8000 }
    );
  };

  const [isClient, setIsClient] = useState(false);
  const [isSendingSOS, setIsSendingSOS] = useState(false);
  const [sosStatus, setSosStatus] = useState<string | null>(null);

  const handleTriggerSOS = async () => {
    setIsSendingSOS(true);
    setSosStatus('Acquiring location & recording emergency alert...');

    let currentCoords = locationCoords;
    if (!currentCoords && typeof window !== 'undefined' && navigator.geolocation) {
      try {
        const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
          navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 4000 });
        });
        currentCoords = {
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
        };
        setLocationCoords(currentCoords);
      } catch {
        // Continue dispatch even without GPS
      }
    }

    try {
      const resp = await triggerEmergencySOS({
        latitude: currentCoords?.latitude,
        longitude: currentCoords?.longitude,
        patient_name: activeProfile?.full_name || 'Emergency Patient',
      });
      setSosStatus(`${resp.message} Reference #${resp.dispatch_id.slice(0, 8)}. Opening the 108 call screen...`);
      setTimeout(() => {
        window.location.href = 'tel:108';
      }, 700);
    } catch {
      setSosStatus('Connecting to 108...');
      window.location.href = 'tel:108';
    } finally {
      setIsSendingSOS(false);
    }
  };

  useEffect(() => {
    setIsClient(true);
    const token = typeof window !== 'undefined' ? (localStorage.getItem('grannus_auth_token') || localStorage.getItem('grannus_user')) : null;
    if (!token) {
      router.replace('/login');
      return;
    }

    const profile = getActiveProfile();
    if (profile) {
      setActiveProfileState(profile);
      if (profile.preferred_language) setLanguage(profile.preferred_language);
      if (profile.age) setAge(String(profile.age));
      if (profile.gender) setGender(profile.gender);
      if (profile.known_conditions?.length) setConditions(profile.known_conditions.join(', '));
      if (profile.medications?.length) setMedications(profile.medications.join(', '));
    }
  }, [router]);

  const executePipelineSubmission = (blobToSubmit: Blob, acceptedConsentId: string) => {
    const payload = {
      audio: blobToSubmit,
      language_code: language,
      doctor_preferred_language: doctorLanguage,
      age,
      gender,
      reported_duration: duration,
      known_conditions: conditions,
      current_medications: medications,
      profile_id: activeProfile?.id,
      patient_name: activeProfile?.full_name,
      latitude: locationCoords?.latitude,
      longitude: locationCoords?.longitude,
      consent_id: acceptedConsentId,
    };

    setPipelinePayload(payload);
    router.push('/processing');
  };

  const handleProcessClick = () => {
    if (!audioBlob) return;
    if (!consentApproved) {
      setShowConsentModal(true);
      return;
    }
    if (consentId) executePipelineSubmission(audioBlob, consentId);
    else setShowConsentModal(true);
  };

  const handleConsentAffirmed = async () => {
    if (isSavingConsent) return;
    setIsSavingConsent(true);
    setConsentError(null);
    try {
      const recorded = await recordPatientConsent({
        language_code: language === 'unknown' ? (activeProfile?.preferred_language || 'en-IN') : language,
        profile_id: activeProfile?.id,
      });
      setConsentId(recorded.consent_id);
      setConsentApproved(true);
      setShowConsentModal(false);
      if (audioBlob) executePipelineSubmission(audioBlob, recorded.consent_id);
    } catch (error) {
      setConsentError(error instanceof Error ? error.message : 'We could not save your consent. Please try again.');
    } finally {
      setIsSavingConsent(false);
    }
  };

  if (!isClient) return null; // Avoid hydration mismatch for tabs

  return (
    <div className="container mx-auto px-4 py-12 max-w-3xl">
      <div className="text-center mb-10 animate-gentle-fade-in">
        <h1 className="text-4xl font-semibold text-foreground mb-3">Voice Input</h1>
        <p className="text-muted-foreground text-lg">Record or upload a patient consultation audio</p>
      </div>

      {/* Emergency SOS support banner with optional GPS sharing */}
      <div className="mb-8 p-4 bg-red-500/10 border-2 border-red-500/30 rounded-2xl flex flex-col sm:flex-row items-center justify-between gap-4 shadow-sm animate-gentle-fade-in">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-red-600 text-white flex items-center justify-center font-bold text-sm shrink-0 animate-pulse">
            SOS
          </div>
          <div>
            <h3 className="text-sm font-bold text-red-900 dark:text-red-200">Severe Medical Emergency?</h3>
            <p className="text-xs text-red-700 dark:text-red-300">
              Chest pain, choking, severe bleeding, or unconsciousness? Do not record audio. Call 108 or 112 now. This app records an alert but cannot dispatch an ambulance.
            </p>
            {sosStatus && (
              <p className="text-xs font-semibold text-red-800 dark:text-red-200 mt-1 animate-pulse">
                {sosStatus}
              </p>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0 w-full sm:w-auto">
          <button
            type="button"
            onClick={handleTriggerSOS}
            disabled={isSendingSOS}
            className="flex-1 sm:flex-initial inline-flex items-center justify-center gap-1.5 px-4 py-2.5 min-h-[44px] rounded-xl bg-red-600 hover:bg-red-700 text-white text-xs font-bold shadow-md transition-colors active:scale-95 cursor-pointer disabled:opacity-75"
          >
            <PhoneCall className="w-3.5 h-3.5" />
            <span>{isSendingSOS ? 'Recording alert...' : 'Record SOS & Call 108'}</span>
          </button>
          <a
            href="tel:112"
            className="inline-flex items-center justify-center gap-1 px-3 py-2.5 min-h-[44px] rounded-xl bg-red-600/20 hover:bg-red-600/30 text-red-700 dark:text-red-300 border border-red-500/40 text-xs font-bold transition-colors"
          >
            112
          </a>
        </div>
      </div>

      {activeProfile ? (
        <div className="mb-8 p-4 bg-primary/10 border border-primary/20 rounded-2xl flex items-center justify-between shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-primary text-primary-foreground flex items-center justify-center font-bold text-sm shadow-sm">
              {activeProfile.full_name.charAt(0).toUpperCase()}
            </div>
            <div>
              <p className="text-[11px] text-muted-foreground uppercase font-semibold tracking-wider">Active Patient Profile</p>
              <h3 className="text-base font-semibold text-foreground">
                {activeProfile.full_name}{' '}
                <span className="text-xs font-normal text-muted-foreground">
                  ({activeProfile.relation}{activeProfile.age ? `, ${activeProfile.age} yrs` : ''})
                </span>
              </h3>
            </div>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => router.push('/login')}
            className="text-xs rounded-xl h-8 font-medium"
          >
            Switch Profile
          </Button>
        </div>
      ) : (
        <div className="mb-8 p-3 bg-muted/60 border border-border rounded-2xl flex items-center justify-between text-xs">
          <span className="text-muted-foreground">Consulting as Guest Patient (No profile selected)</span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => router.push('/login')}
            className="text-xs text-primary font-medium hover:underline h-7"
          >
            Sign In / Choose Family Member →
          </Button>
        </div>
      )}

      <div className="mb-12">
        <Tabs defaultValue="record" className="w-full">
          <TabsList className="grid w-full max-w-sm mx-auto grid-cols-2 mb-8 bg-card border border-border rounded-full p-1 h-12 shadow-sm">
            <TabsTrigger value="record" className="rounded-full text-base data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-md transition-all">Record</TabsTrigger>
            <TabsTrigger value="upload" className="rounded-full text-base data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-md transition-all">Upload</TabsTrigger>
          </TabsList>
          
          {consentApproved ? (
            <>
              <TabsContent value="record" className="mt-0 focus-visible:outline-none focus-visible:ring-0">
                <AudioRecorder onAudioReady={setAudioBlob} />
              </TabsContent>
              <TabsContent value="upload" className="mt-0 focus-visible:outline-none focus-visible:ring-0">
                <FileUploader onAudioReady={setAudioBlob} />
              </TabsContent>
            </>
          ) : (
            <div className="rounded-xl border border-border bg-muted/30 p-8 text-center">
              <p className="mb-4 text-sm text-muted-foreground">Review and accept the privacy notice before recording or uploading patient audio.</p>
              <Button type="button" onClick={() => setShowConsentModal(true)}>Review privacy notice</Button>
            </div>
          )}
        </Tabs>
      </div>

      <div className="bg-card rounded-2xl p-6 md:p-8 shadow-ink-wash border border-border animate-gentle-fade-in" style={{ animationDelay: '0.2s' }}>
        <h2 className="text-xl font-heading font-medium text-foreground mb-6 pb-4 border-b border-border">Configuration</h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
          <div className="space-y-3">
            <Label className="text-sm font-medium text-foreground">Patient Language</Label>
            <Select value={language} onValueChange={(val) => setLanguage(val || 'unknown')}>
              <SelectTrigger className="w-full bg-background rounded-lg border-border h-11 focus:ring-primary" disabled={!consentApproved}>
                <SelectValue placeholder="Select language" />
              </SelectTrigger>
              <SelectContent>
                {SUPPORTED_LANGUAGES.map((lang) => (
                  <SelectItem key={lang.code} value={lang.code}>
                    {lang.name} {lang.nativeName ? `(${lang.nativeName})` : ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          
          <div className="space-y-3">
            <Label className="text-sm font-medium text-foreground">Doctor's Language</Label>
            <Select value={doctorLanguage} onValueChange={(val) => setDoctorLanguage(val || 'en-IN')}>
              <SelectTrigger className="w-full bg-background rounded-lg border-border h-11 focus:ring-primary">
                <SelectValue placeholder="Select language" />
              </SelectTrigger>
              <SelectContent>
                {SUPPORTED_LANGUAGES.filter(l => l.code !== 'unknown').map((lang) => (
                  <SelectItem key={lang.code} value={lang.code}>
                    {lang.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* Permission-based Geolocation with DPDP Notice */}
        <div className="p-4 bg-muted/30 border border-border rounded-xl space-y-2 mb-8">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-foreground flex items-center gap-1.5">
              <MapPin className="w-4 h-4 text-primary" /> Emergency Location Sharing (Optional)
            </span>
            {locationCoords ? (
              <span className="text-[11px] bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 px-2.5 py-0.5 rounded-full font-medium flex items-center gap-1">
                <ShieldCheck className="w-3 h-3" /> Location Attached
              </span>
            ) : (
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={requestLocation}
                disabled={isLocating}
                className="text-xs h-7 rounded-lg gap-1"
              >
                <MapPin className="w-3 h-3" /> {isLocating ? 'Locating...' : 'Share Location'}
              </Button>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            Under India DPDP Act 2023, your coordinates are strictly utilized to coordinate emergency response (108/112) or identify nearest Primary Health Centres.
          </p>
          {locationError && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
              <AlertCircle className="w-3 h-3 shrink-0" /> {locationError}
            </p>
          )}
        </div>

        <div className="border border-border rounded-xl overflow-hidden mb-8 bg-background/50">
          <button 
            onClick={() => setShowContext(!showContext)}
            className="w-full flex items-center justify-between p-4 hover:bg-muted/50 transition-colors focus:outline-none"
          >
            <span className="font-medium text-foreground">Patient Context (Optional)</span>
            {showContext ? <ChevronUp className="w-5 h-5 text-muted-foreground" /> : <ChevronDown className="w-5 h-5 text-muted-foreground" />}
          </button>
          
          <AnimatePresence>
            {showContext && (
              <motion.div 
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="p-4 pt-0 border-t border-border grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-2 mt-4">
                    <Label>Age</Label>
                    <Input type="number" placeholder="e.g. 45" value={age} onChange={e => setAge(e.target.value)} className="bg-background" />
                  </div>
                  <div className="space-y-2 mt-4">
                    <Label>Gender</Label>
                    <Select value={gender} onValueChange={(val) => setGender(val || '')}>
                      <SelectTrigger className="bg-background"><SelectValue placeholder="Select gender" /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="male">Male</SelectItem>
                        <SelectItem value="female">Female</SelectItem>
                        <SelectItem value="other">Other</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <Label>Reported Duration</Label>
                    <Input placeholder="e.g. 3 days, since last week" value={duration} onChange={e => setDuration(e.target.value)} className="bg-background" />
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <Label>Known Conditions</Label>
                    <Input placeholder="e.g. Type 2 Diabetes, Hypertension" value={conditions} onChange={e => setConditions(e.target.value)} className="bg-background" />
                  </div>
                  <div className="space-y-2 md:col-span-2">
                    <Label>Current Medications</Label>
                    <Input placeholder="e.g. Metformin 500mg" value={medications} onChange={e => setMedications(e.target.value)} className="bg-background" />
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-4 border-t border-border">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
            <span>Audio sent for transcription • Clinical records may be retained • Emergency 108/112</span>
          </div>

          <Button 
            size="lg" 
            className="w-full sm:w-auto px-10 h-12 rounded-full text-base font-medium shadow-md transition-transform active:scale-95"
            disabled={!audioBlob}
            onClick={handleProcessClick}
          >
            Process Audio
          </Button>
        </div>
      </div>

      <ConsentDialog
        key={language}
        isOpen={showConsentModal}
        selectedLanguage={language}
        isSubmitting={isSavingConsent}
        error={consentError}
        onConsentGiven={handleConsentAffirmed}
        onConsentDenied={() => { setConsentError(null); setShowConsentModal(false); }}
      />
    </div>
  );
}

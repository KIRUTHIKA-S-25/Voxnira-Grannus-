'use client';

import React, { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ShieldCheck, AlertTriangle, Clock, FileText, CheckCircle2 } from 'lucide-react';

interface ConsentNotice {
  title: string;
  purpose: string;
  retention: string;
  rights: string;
  emergency_disclaimer: string;
  checkbox_label: string;
}

const CONSENT_NOTICES: Record<string, ConsentNotice> = {
  'en-IN': {
    title: 'Patient Consent & Privacy Notice (DPDP Act 2023)',
    purpose: 'Your audio is sent to Sarvam AI to transcribe your speech and support an AI clinical triage summary for your doctor. It is not used for advertising.',
    retention: "The app does not persist a raw audio file after processing. Transcripts and clinical records may be retained for care and applicable obligations. Sarvam's data retention depends on the service agreement and account settings.",
    rights: 'You may submit a request about your personal data to the designated Grievance Officer. Some clinical records may need to be retained.',
    emergency_disclaimer: 'Grannus is a decision support tool, NOT a definitive diagnosis. In medical emergencies, immediately call 108 or 112.',
    checkbox_label: 'I have understood the purpose and grant voluntary consent for voice recording under DPDP Act 2023.',
  },
  'ta-IN': {
    title: 'நோயாளி ஒப்புதல் மற்றும் தனியுரிமை அறிவிப்பு (DPDP சட்டம் 2023)',
    purpose: 'உங்கள் குரல் உரைமாற்றம் செய்யவும், மருத்துவருக்கான AI மருத்துவச் சுருக்கத்தை உருவாக்கவும் Sarvam AI-க்கு அனுப்பப்படுகிறது. இது விளம்பரத்திற்குப் பயன்படுத்தப்படாது.',
    retention: 'செயலாக்கத்திற்குப் பிறகு செயலி மூல ஒலிக் கோப்பைச் சேமிக்காது. உரைமாற்றமும் மருத்துவச் சுருக்கமும் பராமரிப்புக்காக வைக்கப்படலாம். Sarvam-இல் தரவு சேமிப்பு சேவை ஒப்பந்தம் மற்றும் கணக்கு அமைப்புகளைப் பொறுத்தது.',
    rights: 'உங்கள் மருத்துவ தரவுகளை நீக்கவோ அல்லது பார்வையிடவோ உங்களுக்கு முழு உரிமை உண்டு.',
    emergency_disclaimer: 'இது முடிவான சிகிச்சை அல்ல; மருத்துவ அவசரத்திற்கு உடனடியாக 108 அல்லது 112 ஐ அழைக்கவும்.',
    checkbox_label: 'நான் இந்த நோக்கத்தை புரிந்துகொண்டு குரல் பதிவு செய்ய முழு சம்மதம் தெரிவிக்கிறேன்.',
  },
  'hi-IN': {
    title: 'मरीज सहमति एवं गोपनीयता सूचना (DPDP अधिनियम 2023)',
    purpose: 'आपकी आवाज़ को लिखित रूप में बदलने और डॉक्टर के लिए AI चिकित्सा सारांश बनाने हेतु ऑडियो Sarvam AI को भेजा जाता है। इसका उपयोग विज्ञापन के लिए नहीं होता।',
    retention: 'प्रक्रिया पूरी होने के बाद ऐप मूल ऑडियो फ़ाइल को सुरक्षित नहीं रखता। देखभाल के लिए प्रतिलेख और चिकित्सीय सारांश रखे जा सकते हैं। Sarvam में डेटा की अवधि सेवा अनुबंध और खाते की सेटिंग पर निर्भर करती है।',
    rights: 'आपको कभी भी अपना डेटा देखने या हटाने का अनुरोध करने का पूरा अधिकार है।',
    emergency_disclaimer: 'यह अंतिम निदान नहीं है। किसी भी आपात स्थिति में तुरंत 108 या 112 पर कॉल करें।',
    checkbox_label: 'मैंने सभी नियम समझ लिए हैं और आवाज रिकॉर्डिंग के लिए अपनी सहमति देता/देती हूँ।',
  },
  'te-IN': {
    title: 'రోగి సమ్మతి మరియు గోప్యతా నోటీసు (DPDP చట్టం 2023)',
    purpose: 'మీ మాటలను లిఖిత రూపంలోకి మార్చి, వైద్యుడికి AI వైద్య సారాంశాన్ని అందించడానికి మీ ఆడియోను Sarvam AIకు పంపుతాము. ఇది ప్రకటనల కోసం ఉపయోగించబడదు.',
    retention: 'ప్రాసెసింగ్ తర్వాత యాప్ అసలు ఆడియో ఫైల్‌ను నిల్వ చేయదు. సంరక్షణ కోసం ట్రాన్స్‌క్రిప్ట్ మరియు వైద్య సారాంశం నిల్వ చేయబడవచ్చు. Sarvam వద్ద డేటా నిల్వ వ్యవధి సేవా ఒప్పందం మరియు ఖాతా సెట్టింగ్‌లపై ఆధారపడి ఉంటుంది.',
    rights: 'మీ డేటాను ఎప్పుడైనా తొలగించే హక్కు మీకు ఉంది.',
    emergency_disclaimer: 'ఇది తుది వైద్య నిర్ధారణ కాదు. అత్యవసర పరిస్థితుల్లో వెంటనే 108 లేదా 112 కు కాల్ చేయండి.',
    checkbox_label: 'నేను ప్రయోజనాన్ని అర్థం చేసుకున్నాను మరియు వాయిస్ రికార్డింగ్ కోసం నా సమ్మతిని తెలియజేస్తున్నాను.',
  },
};

interface ConsentDialogProps {
  isOpen: boolean;
  onConsentGiven: () => void | Promise<void>;
  onConsentDenied: () => void;
  selectedLanguage?: string;
  isSubmitting?: boolean;
  error?: string | null;
}

export function ConsentDialog({
  isOpen,
  onConsentGiven,
  onConsentDenied,
  selectedLanguage = 'en-IN',
  isSubmitting = false,
  error,
}: ConsentDialogProps) {
  const grievanceOfficerEmail = process.env.NEXT_PUBLIC_GRIEVANCE_OFFICER_EMAIL?.trim();
  const [lang, setLang] = useState<string>(
    CONSENT_NOTICES[selectedLanguage] ? selectedLanguage : 'en-IN'
  );
  const [isChecked, setIsChecked] = useState(false);

  const notice = CONSENT_NOTICES[lang] || CONSENT_NOTICES['en-IN'];

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) onConsentDenied(); }}>
      <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <div className="flex items-center gap-2 text-primary mb-1">
            <ShieldCheck className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
            <span className="text-xs uppercase tracking-wider font-semibold text-emerald-700 dark:text-emerald-400">
              Privacy and consent notice
            </span>
          </div>
          <DialogTitle className="text-xl font-heading font-semibold text-foreground">
            {notice.title}
          </DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            Digital Personal Data Protection Act (India) — Purpose Limitation & Patient Rights
          </DialogDescription>
        </DialogHeader>

        {/* Language Switcher */}
        <div className="flex gap-2 my-2 border-b border-border pb-2">
          {[
            { code: 'en-IN', label: 'English' },
            { code: 'ta-IN', label: 'தமிழ்' },
            { code: 'hi-IN', label: 'हिन्दी' },
            { code: 'te-IN', label: 'తెలుగు' },
          ].map((l) => (
            <button
              key={l.code}
              type="button"
              onClick={() => setLang(l.code)}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${
                lang === l.code
                  ? 'bg-primary text-primary-foreground font-medium'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              }`}
            >
              {l.label}
            </button>
          ))}
        </div>

        {/* Notice Content */}
        <div className="space-y-3 text-sm text-foreground/90 my-2">
          <div className="p-3 bg-muted/40 rounded-lg border border-border/50 flex gap-3">
            <FileText className="w-5 h-5 text-primary shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-xs text-foreground uppercase tracking-wide">Purpose Limitation</p>
              <p className="text-xs text-muted-foreground mt-0.5">{notice.purpose}</p>
            </div>
          </div>

          <div className="p-3 bg-muted/40 rounded-lg border border-border/50 flex gap-3">
            <Clock className="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-medium text-xs text-foreground uppercase tracking-wide">Audio and record handling</p>
              <p className="text-xs text-muted-foreground mt-0.5">{notice.retention}</p>
            </div>
          </div>

          <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg flex gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold text-xs text-amber-800 dark:text-amber-300 uppercase tracking-wide">
                Emergency Hotline Disclaimer
              </p>
              <p className="text-xs text-amber-900/90 dark:text-amber-200 mt-0.5">
                {notice.emergency_disclaimer}
              </p>
            </div>
          </div>

          <div className="text-xs text-muted-foreground px-1">
            <p><strong>Statutory Rights:</strong> {notice.rights}</p>
            <p className="mt-1 text-[11px] text-muted-foreground/80">
              Grievance Officer: {grievanceOfficerEmail ? <code>{grievanceOfficerEmail}</code> : 'Contact your care provider for the designated contact.'}
            </p>
          </div>
        </div>

        {/* Explicit Affirmation Checkbox */}
        <div className="mt-4 pt-3 border-t border-border">
          <label className="flex items-start gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={isChecked}
              onChange={(e) => setIsChecked(e.target.checked)}
              className="mt-1 h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary"
            />
            <span className="text-xs font-medium text-foreground leading-snug">
              {notice.checkbox_label}
            </span>
          </label>
          {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
        </div>

        <DialogFooter className="mt-4 gap-2 sm:gap-0">
          <Button variant="outline" size="sm" onClick={onConsentDenied}>
            Decline
          </Button>
          <Button
            size="sm"
            disabled={!isChecked || isSubmitting}
            onClick={() => onConsentGiven()}
            className="gap-2 bg-emerald-600 hover:bg-emerald-700 text-white"
          >
            <CheckCircle2 className="w-4 h-4" />
            {isSubmitting ? 'Saving consent…' : 'Accept & Continue'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

import type { Metadata } from 'next'
import { Plus_Jakarta_Sans, JetBrains_Mono, Newsreader } from 'next/font/google'
import './globals.css'
import { Navbar } from '@/components/layout/navbar'
import { AdvisoryBanner } from '@/components/layout/demo-banner'
import { Footer } from '@/components/layout/footer'
import { Toaster } from '@/components/ui/sonner'

const plusJakarta = Plus_Jakarta_Sans({
  subsets: ['latin'],
  variable: '--font-plus-jakarta',
  weight: ['300', '400', '500', '600', '700', '800'],
  display: 'swap',
})
const jetbrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains',
  display: 'swap',
})
const newsreader = Newsreader({
  subsets: ['latin'],
  variable: '--font-newsreader',
  weight: ['400', '500', '600'],
  style: ['normal', 'italic'],
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'Grannus RuralCare AI — Clinical Voice Triage & Telemedicine Platform',
  description: 'AI-assisted clinical voice triage and specialist routing for rural primary healthcare across 11 Indian languages.',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${plusJakarta.variable} ${jetbrainsMono.variable} ${newsreader.variable} font-sans antialiased min-h-screen flex flex-col bg-background text-foreground`}>
        <AdvisoryBanner />
        <Navbar />
        <main className="flex-1">
          {children}
        </main>
        <Footer />
        <Toaster />
      </body>
    </html>
  )
}

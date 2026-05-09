import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import { ThemeProvider } from "next-themes";
import { Toaster } from "sonner";
import "./globals.css";
import { Sidebar } from "@/components/nav";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: { default: "trader", template: "%s — trader" },
  description: "Minervini-style breakout trading discipline tool. Pre-trade tickets, streak-scaled sizing, auto stops, screener, and behavioral guardrails.",
  keywords: ["trading", "Minervini", "SEPA", "breakout", "screener", "Questrade"],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full bg-background">
        <ClerkProvider>
          <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
            <Sidebar />
            <div className="lg:pl-56 min-h-screen">
              {children}
            </div>
            <Toaster richColors position="bottom-right" closeButton />
          </ThemeProvider>
        </ClerkProvider>
      </body>
    </html>
  );
}

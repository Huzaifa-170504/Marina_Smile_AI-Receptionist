import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "Marina Smile AI Receptionist",
    template: "%s | Marina Smile AI",
  },
  description: "Production-minded AI dental receptionist with grounded Gemini responses, Supabase authorization, verified scheduling, and a role-aware operations dashboard.",
  keywords: ["AI receptionist", "FastAPI", "Next.js", "Supabase", "Gemini", "Google Calendar"],
  openGraph: {
    title: "Marina Smile AI Receptionist",
    description: "A secure, grounded AI clinic operations platform.",
    type: "website",
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}

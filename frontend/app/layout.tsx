import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VoiceOS",
  description: "VoiceOS enterprise platform — Admin and Client dashboards.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="h-full antialiased"
      style={{ fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif' }}
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}

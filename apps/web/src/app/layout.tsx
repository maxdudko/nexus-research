import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Nexus Research",
  description: "Ask a research question and follow the run as it streams.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import { Geist } from "next/font/google";

import { themeInitScript } from "@/lib/theme";

import "./globals.css";

const geist = Geist({
  subsets: ["latin"],
  variable: "--font-geist",
});

export const metadata: Metadata = {
  title: "Nexus Research",
  description: "Ask a research question and follow the run as it streams.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geist.variable} h-full`} suppressHydrationWarning>
      <body className={`${geist.className} min-h-full`}>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        {children}
      </body>
    </html>
  );
}

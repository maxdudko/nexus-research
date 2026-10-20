import type { Metadata } from "next";

import { ThemeToggle } from "@/components/theme-toggle/theme-toggle";
import { themeInitScript } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "Nexus Research",
  description: "Ask a research question and follow the run as it streams.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full" suppressHydrationWarning>
      <body className="relative min-h-full">
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <div className="pointer-events-none absolute inset-x-0 top-0 z-10">
          <div className="mx-auto flex w-full max-w-2xl justify-end px-4 pt-8">
            <div className="pointer-events-auto">
              <ThemeToggle />
            </div>
          </div>
        </div>
        {children}
      </body>
    </html>
  );
}

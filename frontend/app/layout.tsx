import type { Metadata } from "next";
import { IBM_Plex_Mono, Mona_Sans } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

/*
 * Mona Sans carries the whole interface: headings, body, and the urgency
 * numerals. It is a variable family with both a weight and a *width* axis, and
 * the width axis is why it suits this console -- the queue can run its numerals
 * slightly condensed (see `.numeral` in globals.css) so a column of scores lines
 * up tightly without dropping to a smaller size, while prose stays at normal
 * width and full legibility.
 *
 * One family for the UI also means one font request instead of two, which is
 * not nothing for a coordinator on bad wifi.
 *
 * IBM Plex Mono stays, and only for data compared character by character:
 * coordinates, timestamps, request and trace ids. Nowhere else.
 */
const monaSans = Mona_Sans({
  subsets: ["latin"],
  variable: "--font-mona",
  display: "swap",
  axes: ["wdth"],
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "FloodRelay",
  description:
    "Flood-relief coordination console. Nothing is dispatched without a human decision.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${monaSans.variable} ${plexMono.variable}`}>
      <body className="bg-depth text-ink antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}

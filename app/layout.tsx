import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CheckMate — Calculus 1 exam grader",
  description:
    "An autonomous agent that grades Calculus 1 exams — score, partial credit, and written feedback per question.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "三术命盘研究台",
  description: "匿名的紫微、八字与吠陀研究式分析工具。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

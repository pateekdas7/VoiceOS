"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { NavItem } from "@/lib/nav";

export function NavLinks({ items, basePath }: { items: NavItem[]; basePath: string }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-col gap-0.5 px-3">
      {items.map((item) => {
        const href = `${basePath}${item.href}`;
        const isActive = pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            className={`rounded-md px-3 py-2 text-sm transition-colors ${
              isActive
                ? "bg-white/10 font-medium text-sidebar-foreground-active"
                : "text-sidebar-foreground hover:bg-white/5 hover:text-sidebar-foreground-active"
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

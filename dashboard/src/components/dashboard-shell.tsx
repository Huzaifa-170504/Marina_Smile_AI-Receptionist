"use client";

import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { LogoutButton } from "@/components/auth/logout-button";
import { StaffProvider } from "@/components/staff-context";
import { navigationForRole } from "@/lib/navigation";
import { roleLabel } from "@/lib/permissions";
import type { StaffProfile } from "@/types/domain";

export function DashboardShell({ staff, children }: { staff: StaffProfile; children: ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const navigation = navigationForRole(staff.role);

  return (
    <StaffProvider staff={staff}>
      <div className="dashboard-shell">
        <button aria-label="Close navigation" className={`mobile-overlay ${open ? "open" : ""}`} onClick={() => setOpen(false)} />
        <aside className={`sidebar ${open ? "open" : ""}`}>
          <div className="sidebar-brand">
            <span className="sidebar-mark">MS</span>
            <span><strong className="brand-title">Marina Smile</strong><br /><small>Clinic operations</small></span>
          </div>
          <nav className="nav-list" aria-label="Dashboard navigation">
            {navigation.map(({ href, label, icon: Icon }) => {
              const active = href === "/dashboard" ? pathname === href : pathname.startsWith(href);
              return (
                <Link key={href} href={href} className={`nav-link ${active ? "active" : ""}`} onClick={() => setOpen(false)}>
                  <Icon size={17} aria-hidden="true" /> {label}
                </Link>
              );
            })}
          </nav>
          <div className="sidebar-footer">
            <small style={{ color: "#8eb7b4" }}>Signed in as</small>
            <p style={{ margin: ".3rem 0 .8rem", fontWeight: 750 }}>{staff.fullName}</p>
            <Link href="/dashboard/account/security" className="sidebar-security-link">Account security</Link>
            <LogoutButton />
          </div>
        </aside>
        <div className="main-column">
          <header className="topbar">
            <button className="icon-button menu-button" aria-label="Open navigation" onClick={() => setOpen(true)}>
              {open ? <X size={20} /> : <Menu size={20} />}
            </button>
            <div><strong>Operational workspace</strong><br /><small className="muted">Live clinic state</small></div>
            <div className="topbar-meta">
              <span className="topbar-name"><strong>{staff.fullName}</strong></span>
              <span className="role-badge">{roleLabel(staff.role)}</span>
            </div>
          </header>
          <main className="page-content">{children}</main>
        </div>
      </div>
    </StaffProvider>
  );
}

import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { BarChart3, Bell, BookOpenCheck, Brain, CalendarDays, Cpu, FolderKanban, LayoutDashboard, Library, Moon, Palette, Settings as Cog, Sun } from 'lucide-react'
import { api, CalEntry } from './api'
import { ConfirmProvider, Logo, ToastProvider, useApi, useToast, fmtDate } from './ui'
import Dashboard from './pages/Dashboard'
import Projects from './pages/Projects'
import ProjectDetail from './pages/ProjectDetail'
import LibraryPage from './pages/Library'
import Review from './pages/Review'
import MemoryPage from './pages/Memory'
import Processing from './pages/Processing'
import Brand from './pages/Brand'
import Calendar from './pages/Calendar'
import Analytics from './pages/Analytics'
import SettingsPage from './pages/Settings'

const NAV = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/library', label: 'Content Library', icon: Library },
  { to: '/review', label: 'Review', icon: BookOpenCheck },
  { to: '/memory', label: 'AI Memory', icon: Brain },
  { to: '/processing', label: 'Processing Centre', icon: Cpu },
  { to: '/calendar', label: 'Calendar', icon: CalendarDays },
  { to: '/brand', label: 'Brand & Templates', icon: Palette },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/settings', label: 'Settings', icon: Cog },
]

function useTheme() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('ruha-theme') as any) || 'dark')
  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); localStorage.setItem('ruha-theme', theme) }, [theme])
  return [theme, setTheme] as const
}

function Reminders() {
  const toast = useToast()
  const { data, reload } = useApi(() => api.get<CalEntry[]>('/api/reminders/due'), [], 60000)
  const [open, setOpen] = useState(false)
  const n = data?.length ?? 0
  return (
    <div className="relative">
      <button className="btn-ghost !p-2 relative" onClick={() => setOpen(!open)} aria-label={`Reminders (${n})`} title="Calendar reminders">
        <Bell size={18} />{n > 0 && <span className="absolute -top-1 -right-1 h-4 min-w-4 px-1 rounded-full bg-danger text-[10px] text-white flex items-center justify-center">{n}</span>}
      </button>
      {open && (
        <div className="card absolute right-0 mt-2 w-80 p-3 z-40 shadow-2xl">
          <p className="text-sm font-medium mb-2">Due reminders</p>
          {n === 0 ? <p className="text-sm text-muted">Nothing due. Set reminders on calendar entries.</p> : data!.map((e) => (
            <div key={e.id} className="flex items-start gap-2 py-2 border-t border-line/60 first:border-0">
              <div className="flex-1"><p className="text-sm">{e.title}</p><p className="text-xs text-muted">{fmtDate(e.scheduled_for)}</p></div>
              <button className="btn-ghost !py-1" onClick={async () => { try { await api.post(`/api/reminders/${e.id}/done`); reload() } catch (x) { toast.err(x) } }}>Done</button>
            </div>))}
        </div>
      )}
    </div>
  )
}

function QueueChip() {
  const { data } = useApi(() => api.get<any>('/api/dashboard'), [], 4000)
  const p = data?.processing
  if (!p) return null
  const busy = p.active > 0 || p.queued > 0
  return (
    <NavLink to="/processing" className="hidden sm:flex items-center gap-2 text-xs card px-3 py-1.5" title="Open the Processing Centre">
      <span className={`h-2 w-2 rounded-full ${p.paused ? 'bg-warn' : busy ? 'bg-accent animate-pulse' : 'bg-ok'}`} />
      {p.paused ? 'Queue paused' : busy ? `${p.active} running · ${p.queued} queued` : 'Idle'}
      {p.failed > 0 && <span className="text-danger">· {p.failed} failed</span>}
    </NavLink>
  )
}

function Shell() {
  const [theme, setTheme] = useTheme()
  return (
    <div className="min-h-full flex">
      <aside className="hidden md:flex w-60 shrink-0 flex-col border-r border-line/60 bg-surface/60 backdrop-blur sticky top-0 h-screen p-4">
        <div className="flex items-center gap-3 px-2 mb-6">
          <Logo />
          <div className="leading-tight"><p className="font-display font-semibold text-lg">RUHA <span className="text-accent">Manager</span></p><p className="text-[10px] text-muted">Create. Manage. Inspire.</p></div>
        </div>
        <nav className="flex flex-col gap-1 overflow-y-auto" aria-label="Main">
          {NAV.map(({ to, label, icon: I, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition ${isActive ? 'bg-gradient-to-r from-primary/30 to-accent/10 text-ink shadow-glow' : 'text-muted hover:text-ink hover:bg-surface2/70'}`}>
              <I size={18} />{label}
            </NavLink>))}
        </nav>
        <p className="mt-auto text-[10px] text-muted px-2">RUHA AI Video &amp; Content Studio<br />Runs fully on this computer.</p>
      </aside>
      <div className="flex-1 min-w-0 flex flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 px-4 md:px-8 py-3 border-b border-line/50 bg-bg/70 backdrop-blur">
          <div className="md:hidden flex items-center gap-2"><Logo size={28} /><span className="font-display font-semibold">RUHA</span></div>
          <div className="flex-1" />
          <QueueChip /><Reminders />
          <button className="btn-ghost !p-2" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="Toggle light/dark mode" title="Toggle light/dark mode">
            {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}</button>
        </header>
        <nav className="md:hidden flex gap-1 overflow-x-auto px-3 py-2 border-b border-line/50" aria-label="Main (compact)">
          {NAV.map(({ to, label, icon: I, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `chip shrink-0 ${isActive ? 'bg-primary/25 border-primary/50' : 'border-line/60 text-muted'}`}><I size={13} />{label}</NavLink>))}
        </nav>
        <main className="flex-1 px-4 md:px-8 py-6 max-w-[1500px] w-full mx-auto animate-fadeUp">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/projects" element={<Projects />} />
            <Route path="/projects/:id" element={<ProjectDetail />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/review" element={<Review />} />
            <Route path="/memory" element={<MemoryPage />} />
            <Route path="/processing" element={<Processing />} />
            <Route path="/calendar" element={<Calendar />} />
            <Route path="/brand" element={<Brand />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return <BrowserRouter><ToastProvider><ConfirmProvider><Shell /></ConfirmProvider></ToastProvider></BrowserRouter>
}

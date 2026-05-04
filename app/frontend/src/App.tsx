import { NavLink, Outlet } from 'react-router-dom'
import { ChatSessionProvider } from './lib/chatSession'

const FEEDBACK_URL = 'https://github.com/1797127235/CareerOS/issues'

function App() {
  return (
    <ChatSessionProvider>
      <div className="min-h-screen flex flex-col">
        <header className="sticky top-0 z-50 h-[64px] flex items-center justify-between px-md md:px-lg border-b border-border bg-bg/80 backdrop-blur-md">
          <NavLink
            to="/"
            end
            className="flex items-baseline gap-xs text-text hover:opacity-100"
          >
            <span className="text-xl font-han text-ink">CareerOS</span>
          </NavLink>

          <nav className="flex items-center gap-lg text-base">
            <NavItem to="/" end>对话</NavItem>
            <NavItem to="/profile">画像</NavItem>
            <NavItem to="/memories">记忆</NavItem>
            <NavItem to="/settings">设置</NavItem>
            <a
              href={FEEDBACK_URL}
              target="_blank"
              rel="noreferrer noopener"
              className="text-text-muted hover:text-text"
            >
              反馈
            </a>
          </nav>
        </header>

        <main className="flex-1 w-full ink-fade-in">
          <Outlet />
        </main>
      </div>
    </ChatSessionProvider>
  )
}

function NavItem({
  to,
  end,
  children,
}: {
  to: string
  end?: boolean
  children: React.ReactNode
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        isActive ? 'text-ink underline underline-offset-[6px] decoration-1' : 'text-text-muted hover:text-text'
      }
    >
      {children}
    </NavLink>
  )
}

export default App

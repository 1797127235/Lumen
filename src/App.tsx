import { useEffect, useRef, useState } from 'react'
import { Outlet, useLocation, useSearchParams } from 'react-router-dom'
import { ChatSessionProvider, useChatSession } from './lib/chatSession'
import Sidebar from './components/Sidebar'
import Settings from './pages/Settings'

function ChatUrlSync() {
  const loc = useLocation()
  const isChat = loc.pathname === '/'
  const { conversationId, messages, streaming, loadConversation } = useChatSession()
  const [, setSP] = useSearchParams()
  const prev = useRef(isChat)

  useEffect(() => {
    if (isChat && conversationId) setSP({ c: conversationId }, { replace: true })
  }, [isChat, conversationId, setSP])

  useEffect(() => {
    const entered = isChat && !prev.current
    prev.current = isChat
    if (!entered || streaming || messages.length > 0) return
    const cid = new URLSearchParams(loc.search).get('c')
    if (cid) void loadConversation(cid)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isChat])

  return null
}

export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false)

  useEffect(() => {
    const handler = () => setSettingsOpen(true)
    window.addEventListener('lumen-open-settings', handler)
    return () => window.removeEventListener('lumen-open-settings', handler)
  }, [])

  return (
    <ChatSessionProvider>
      <ChatUrlSync />
      <div className="flex h-screen">
        <Sidebar />
        <main className="flex-1 min-w-0 h-full overflow-y-auto"><Outlet /></main>
      </div>
      <Settings isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </ChatSessionProvider>
  )
}

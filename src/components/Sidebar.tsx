import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { deleteConversation, getChatHistory, type ConversationSummary } from '../lib/api'
import { useChatSession } from '../lib/chatSession'

function groupByDate(items: ConversationSummary[]): { label: string; items: ConversationSummary[] }[] {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)
  const weekAgo = new Date(today.getTime() - 6 * 86400000)

  const groups: Record<string, ConversationSummary[]> = {
    TODAY: [],
    昨天: [],
    本周: [],
    更早: [],
  }

  for (const item of items) {
    if (!item.last_message_at) { groups['更早'].push(item); continue }
    const d = new Date(item.last_message_at)
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate())
    if (day >= today) groups['TODAY'].push(item)
    else if (day >= yesterday) groups['昨天'].push(item)
    else if (day >= weekAgo) groups['本周'].push(item)
    else groups['更早'].push(item)
  }

  return Object.entries(groups)
    .filter(([, list]) => list.length > 0)
    .map(([label, list]) => ({ label, items: list }))
}

function formatTime(iso: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const now = new Date()
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate()
  if (sameDay) {
    const h = date.getHours()
    const m = date.getMinutes()
    return `${h < 10 ? '0' + h : h}:${m < 10 ? '0' + m : m}`
  }
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

export default function Sidebar() {
  const { conversationId, loadConversation, startNew } = useChatSession()
  const [items, setItems] = useState<ConversationSummary[]>([])
  const [deleteId, setDeleteId] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    getChatHistory(30)
      .then(setItems)
      .catch(() => {})
  }, [conversationId])

  async function handleDelete(id: string, event: React.MouseEvent) {
    event.stopPropagation()
    if (deleteId !== id) {
      setDeleteId(id)
      setTimeout(() => setDeleteId(prev => prev === id ? null : prev), 3000)
      return
    }
    try {
      await deleteConversation(id)
      setItems(prev => prev.filter(i => i.conversation_id !== id))
      setDeleteId(null)
      if (id === conversationId) {
        startNew()
        navigate('/', { replace: true })
      }
    } catch {
      alert('删除失败，请重试')
    }
  }

  const groups = groupByDate(items)

  const navLinkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-sm px-sm py-xs rounded-md text-sm transition-colors ${
      isActive
        ? 'bg-surface-elevated !text-text'
        : '!text-text-muted hover:bg-surface hover:!text-text'
    }`

  return (
    <aside className="flex min-h-0 w-[220px] flex-shrink-0 flex-col border-r border-border-soft bg-surface px-xs py-md gap-xs overflow-hidden">
      {/* Logo */}
      <NavLink
        to="/"
        end
        className="px-sm py-xs text-xl font-han text-ink hover:opacity-80 mb-xs"
      >
        Lumen
      </NavLink>

      {/* 新对话 */}
      <button
        onClick={() => { startNew(); navigate('/', { replace: true }) }}
        className="flex items-center gap-xs px-sm py-xs rounded-md text-sm text-text-muted hover:bg-surface-elevated hover:text-text transition-colors"
      >
        <span className="text-base leading-none">＋</span>
        新对话
      </button>

      {/* 对话历史 */}
      <div className="scroll-auto-hide flex min-h-0 flex-1 flex-col gap-xs mt-xs overflow-y-auto">
        {groups.map(group => (
          <div key={group.label}>
            <div className="px-sm py-xs text-xs text-text-subtle">{group.label}</div>
            {group.items.map(item => (
              <div
                key={item.conversation_id}
                onClick={() => { void loadConversation(item.conversation_id); navigate('/', { replace: true }) }}
                className={`group relative flex items-center rounded-md px-sm py-[6px] cursor-pointer transition-colors ${
                  conversationId === item.conversation_id
                    ? 'bg-surface-elevated/50'
                    : 'hover:bg-surface-elevated/30'
                }`}
              >
                <div className="flex-1 min-w-0">
                  <div className="truncate text-sm text-text group-hover:text-ink transition-colors">
                    {item.title || '未命名'}
                  </div>
                  <div className="text-xs text-text-subtle">{formatTime(item.last_message_at)}</div>
                </div>
                <button
                  onClick={(e) => void handleDelete(item.conversation_id, e)}
                  className={`ml-xs flex-shrink-0 text-xs transition-all ${
                    deleteId === item.conversation_id
                      ? 'text-danger opacity-100'
                      : 'text-text-subtle opacity-0 group-hover:opacity-100 hover:text-danger'
                  }`}
                >
                  {deleteId === item.conversation_id ? '确定？' : '删'}
                </button>
              </div>
            ))}
          </div>
        ))}
        {items.length === 0 && (
          <p className="px-sm text-xs text-text-subtle">还没有聊过。</p>
        )}
      </div>

      {/* 页面导航 */}
      <div className="flex flex-col gap-2xs border-t border-border-soft pt-xs mt-xs">
        <NavLink to="/profile" className={navLinkClass}>画像</NavLink>
        <NavLink to="/knowledge" className={navLinkClass}>文档管理</NavLink>
        <NavLink to="/memories" className={navLinkClass}>记忆</NavLink>
      </div>

      {/* 底部设置 */}
      <div className="border-t border-border-soft pt-xs mt-xs">
        <NavLink to="/settings" className={navLinkClass}>
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
          设置
        </NavLink>
      </div>
    </aside>
  )
}

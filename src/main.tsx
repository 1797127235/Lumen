import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import 'lxgw-wenkai-screen-webfont/style.css'
import '@fontsource-variable/alegreya/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import '@fontsource/caveat/400.css'
import './index.css'
import App from './App.tsx'
import Chat from './pages/Chat.tsx'
import Profile from './pages/Profile.tsx'
import Knowledge from './pages/Knowledge.tsx'
import Memories from './pages/Memories.tsx'
import Settings from './pages/Settings.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<Chat />} />
          <Route path="profile" element={<Profile />} />
          <Route path="jd" element={<Navigate to="/" replace />} />
          <Route path="jd/:id" element={<Navigate to="/" replace />} />
          <Route path="targets" element={<Navigate to="/" replace />} />
          <Route path="targets/:id" element={<Navigate to="/" replace />} />
          <Route path="knowledge" element={<Knowledge />} />
          <Route path="memories" element={<Memories />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)

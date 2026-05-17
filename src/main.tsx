import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import 'lxgw-wenkai-screen-webfont/style.css'
import '@fontsource-variable/alegreya/index.css'
import '@fontsource-variable/jetbrains-mono/index.css'
import '@fontsource/caveat/400.css'
import './index.css'
import App from './App.tsx'
import Chat from './pages/Chat.tsx'
import Profile from './pages/Profile.tsx'

import MyWorld from './pages/MyWorld.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<App />}>
          <Route index element={<Chat />} />
          <Route path="profile" element={<Profile />} />

          <Route path="my-world" element={<MyWorld />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
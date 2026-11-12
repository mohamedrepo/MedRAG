import React, { useEffect, useRef, useState } from 'react'
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, getToken, setToken } from './api'
import { I18nProvider, useI18n } from './i18n'
import SearchPage from './pages/Search'
import AdminPage from './pages/Admin'
import LoginPage from './pages/Login'

function Topbar() {
  const { t, lang, setLang } = useI18n()
  const nav = useNavigate()
  const loc = useLocation()
  const [logged, setLogged] = useState(!!getToken())
  useEffect(() => {
    setLogged(!!getToken())
  }, [loc.pathname])
  return (
    <header className="topbar">
      <Link to="/" className="brand" style={{ color: '#fff', textDecoration: 'none' }}>
        <span className="pulse" /> MedRAG
      </Link>
      <nav>
        <Link to="/" className={loc.pathname === '/' ? 'active' : ''}>{t('home')}</Link>
        {logged && <Link to="/admin" className={loc.pathname.startsWith('/admin') ? 'active' : ''}>{t('admin')}</Link>}
      </nav>
      <div className="spacer" />
      <button className="lang-btn" onClick={() => setLang(lang === 'ar' ? 'en' : 'ar')}>
        {lang === 'ar' ? 'English' : 'العربية'}
      </button>
      {logged ? (
        <button className="ghost-btn" onClick={() => { setToken(null); setLogged(false); nav('/') }}>
          {t('logout')}
        </button>
      ) : (
        <Link to="/login" className="ghost-btn" style={{ color: '#fff' }}>{t('login')}</Link>
      )}
    </header>
  )
}

export default function App() {
  return (
    <I18nProvider>
      <div className="app-shell">
        <Topbar />
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/admin/*" element={<AdminPage />} />
        </Routes>
        <footer className="footer">
          MedRAG · Unified medical price intelligence · PostgreSQL-ready · Bilingual AR/EN
        </footer>
      </div>
    </I18nProvider>
  )
}

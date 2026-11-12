import { useState } from 'react'
import { api, setToken } from '../api'
import { useI18n } from '../i18n'
import { useNavigate } from 'react-router-dom'

export default function LoginPage() {
  const { t } = useI18n()
  const nav = useNavigate()
  const [u, setU] = useState('admin')
  const [p, setP] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    try {
      const r = await api.post<{ access_token: string; refresh_token: string }>('/auth/login', {
        username: u,
        password: p,
      })
      setToken(r.access_token)
      nav('/admin')
    } catch (e: any) {
      setErr(e.message || 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="page">
      <div className="card login-wrap">
        <h2>{t('login')}</h2>
        <form onSubmit={submit} className="form-grid" style={{ gridTemplateColumns: '1fr' }}>
          <div className="field">
            <label>{t('username')}</label>
            <input value={u} onChange={e => setU(e.target.value)} required />
          </div>
          <div className="field">
            <label>{t('password')}</label>
            <input type="password" value={p} onChange={e => setP(e.target.value)} required />
          </div>
          {err && <div className="badge danger">{err}</div>}
          <button className="btn" disabled={busy}>{t('login')}</button>
        </form>
        <p className="muted" style={{ marginTop: 12 }}>
          Default: admin / medrag-admin (change via .env)
        </p>
      </div>
    </div>
  )
}

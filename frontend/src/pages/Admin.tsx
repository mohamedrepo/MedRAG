import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { useI18n } from '../i18n'

interface Stats {
  documents: number
  examinations: number
  approved_aliases: number
  proposed_aliases: number
  price_records: number
  price_values: number
  providers: number
  organizations: number
  needs_review: number
  pending_reviews: number
}

export default function AdminPage() {
  const { t, lang } = useI18n()
  const nav = useNavigate()
  const [tab, setTab] = useState('overview')
  const [stats, setStats] = useState<Stats | null>(null)
  const [docs, setDocs] = useState<any[]>([])
  const [reviews, setReviews] = useState<any[]>([])
  const [aliases, setAliases] = useState<any[]>([])
  const [exams, setExams] = useState<any[]>([])
  const [msg, setMsg] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [q, setQ] = useState('')

  React.useEffect(() => {
    if (!localStorage.getItem('medrag.token')) {
      nav('/login')
      return
    }
    refresh()
  }, [tab])

  async function refresh() {
    try {
      const s = await api.get<Stats>('/stats')
      setStats(s)
      if (tab === 'documents') setDocs((await api.get<any>('/documents?limit=100')).items)
      if (tab === 'reviews') setReviews((await api.get<any>('/admin/reviews?limit=100')).items)
      if (tab === 'aliases') setAliases((await api.get<any>('/admin/aliases?limit=100')).items)
      if (tab === 'catalog') setExams((await api.get<any>('/examinations?limit=100')).items)
    } catch (e: any) {
      if (e.message === '401') nav('/login')
      setMsg(e.message)
    }
  }

  async function upload() {
    if (!file) return
    setMsg('Uploading…')
    try {
      const r = await api.upload<any>('/documents/upload', file)
      setMsg(JSON.stringify(r.stats || r).slice(0, 200))
      refresh()
    } catch (e: any) {
      setMsg(e.message)
    }
  }

  async function ingestFolder() {
    setMsg('Ingesting folder…')
    try {
      const r = await api.post<any>('/documents/ingest-folder', { path: 'E:/MedRAG', recursive: true })
      setMsg(`Ingested ${r.count} files`)
      refresh()
    } catch (e: any) {
      setMsg(e.message)
    }
  }

  async function approveAlias(id: string) {
    await api.post(`/examinations/aliases/${id}/approve`)
    refresh()
  }
  async function rejectAlias(id: string) {
    await api.post(`/examinations/aliases/${id}/reject`)
    refresh()
  }
  async function resolveReview(id: string, action?: string) {
    await api.post(`/admin/reviews/${id}/resolve`, { action })
    refresh()
  }

  const tabs = [
    ['overview', t('stats')],
    ['documents', t('documents')],
    ['reviews', t('reviews')],
    ['aliases', t('aliases')],
    ['catalog', t('catalog')],
  ]

  return (
    <div className="page">
      <div className="row between" style={{ marginBottom: 14 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>{t('admin')}</h1>
        <Link to="/" className="btn secondary small">{t('backToSearch')}</Link>
      </div>

      <div className="tabs">
        {tabs.map(([k, label]) => (
          <button key={k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {msg && <div className="card"><span className="muted">{msg}</span></div>}

      {tab === 'overview' && stats && (
        <div className="grid-stats">
          <Stat n={stats.documents} l={t('documentsIngested')} />
          <Stat n={stats.examinations} l={t('canonicalExams')} />
          <Stat n={stats.approved_aliases} l={`${t('aliases')} · ${t('approved')}`} />
          <Stat n={stats.proposed_aliases} l={`${t('aliases')} · ${t('proposed')}`} />
          <Stat n={stats.price_records} l={t('priceRecords')} />
          <Stat n={stats.price_values} l={lang === 'ar' ? 'قيم سعرية' : 'Price values'} />
          <Stat n={stats.providers} l={t('providers')} />
          <Stat n={stats.pending_reviews} l={t('pendingReviews')} />
        </div>
      )}

      {tab === 'documents' && (
        <div className="card">
          <h2>{t('upload')}</h2>
          <div className="row">
            <input type="file" onChange={e => setFile(e.target.files?.[0] ?? null)} />
            <button className="btn small" onClick={upload} disabled={!file}>{t('upload')}</button>
            <button className="btn secondary small" onClick={ingestFolder}>{t('ingestFolder')}</button>
          </div>
          <div className="scroll-x" style={{ marginTop: 14 }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>File</th><th>Year</th><th>{t('extraction')}</th><th>Provider</th><th>Pages</th><th>Uploaded</th>
                </tr>
              </thead>
              <tbody>
                {docs.map(d => (
                  <tr key={d.id}>
                    <td>{d.original_filename}</td>
                    <td>{d.tariff_year}</td>
                    <td><span className={`badge ${d.extraction_status === 'completed' ? 'ok' : d.extraction_status === 'failed' ? 'danger' : 'warn'}`}>{d.extraction_status}</span></td>
                    <td>{d.provider_id ? '✓' : <span className="badge warn">{t('providerUnspecified')}</span>}</td>
                    <td>{d.page_count}</td>
                    <td className="muted">{new Date(d.uploaded_at).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'reviews' && (
        <div className="card">
          <h2>{t('reviews')} ({reviews.length})</h2>
          <div className="scroll-x">
            <table className="tbl">
              <thead>
                <tr><th>Priority</th><th>Type</th><th>Reason</th><th>Detail</th><th></th></tr>
              </thead>
              <tbody>
                {reviews.map(r => (
                  <tr key={r.id}>
                    <td>{r.priority}</td>
                    <td>{r.entity_type}</td>
                    <td>{r.reason}</td>
                    <td className="mono" style={{ maxWidth: 320 }}>{JSON.stringify(r.detail).slice(0, 180)}</td>
                    <td className="row">
                      <button className="btn small" onClick={() => resolveReview(r.id, 'approve_mapping')}>{t('approve')}</button>
                      <button className="btn danger small" onClick={() => resolveReview(r.id, 'reject_record')}>{t('reject')}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!reviews.length && <div className="empty">Empty ✓</div>}
          </div>
        </div>
      )}

      {tab === 'aliases' && (
        <div className="card">
          <h2>{t('aliases')} · {t('proposed')} ({aliases.length})</h2>
          <div className="scroll-x">
            <table className="tbl">
              <thead>
                <tr><th>Alias</th><th>Exam</th><th>Lang</th><th>Type</th><th>{t('confidence')}</th><th></th></tr>
              </thead>
              <tbody>
                {aliases.map(a => (
                  <tr key={a.id}>
                    <td>{a.alias}</td>
                    <td>{a.exam_en}<div className="muted">{a.exam_ar}</div></td>
                    <td>{a.language}</td>
                    <td>{a.type}</td>
                    <td>{(a.confidence * 100).toFixed(0)}%</td>
                    <td className="row">
                      <button className="btn small" onClick={() => approveAlias(a.id)}>{t('approve')}</button>
                      <button className="btn danger small" onClick={() => rejectAlias(a.id)}>{t('reject')}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!aliases.length && <div className="empty">Empty ✓</div>}
          </div>
        </div>
      )}

      {tab === 'catalog' && (
        <div className="card">
          <h2>{t('catalog')}</h2>
          <div className="row" style={{ marginBottom: 10 }}>
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="CBC, MRI, صورة دم…" style={{ flex: 1, padding: 8, border: '1px solid var(--border)', borderRadius: 8 }} />
            <button className="btn small" onClick={async () => setExams((await api.get<any>('/examinations?q=' + encodeURIComponent(q) + '&limit=100')).items)}>Search</button>
          </div>
          <div className="scroll-x">
            <table className="tbl">
              <thead>
                <tr><th>Code</th><th>EN</th><th>AR</th><th>Category</th><th>Aliases</th></tr>
              </thead>
              <tbody>
                {exams.map(e => (
                  <tr key={e.id}>
                    <td className="mono">{e.code}</td>
                    <td>{e.name_en}</td>
                    <td>{e.name_ar}</td>
                    <td>{e.category}</td>
                    <td>{e.alias_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Stat({ n, l }: { n: number; l: string }) {
  return (
    <div className="stat">
      <div className="n">{n?.toLocaleString?.() ?? n}</div>
      <div className="l">{l}</div>
    </div>
  )
}

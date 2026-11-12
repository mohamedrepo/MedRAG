import React, { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useI18n } from '../i18n'

interface Interpretation {
  user_term: string
  normalized: string
  canonical_code: string
  canonical_en: string
  canonical_ar: string | null
  category: string | null
  confidence: number
  strategy: string
  aliases: { alias: string; language: string; type: string; status: string }[]
}

interface Row {
  rank: number
  price: number | null
  currency: string
  price_category: string
  original_name: string
  original_code: string | null
  provider_name: string | null
  provider_name_ar: string | null
  branch: string | null
  city: string | null
  governorate: string | null
  provider_unspecified: boolean
  tariff_year: number | null
  effective_date: string | null
  exam_code: string
  exam_en: string | null
  exam_ar: string | null
  source_page: number | null
  source_sheet: string | null
  review_status: string
  extraction_confidence: number
  document_title: string | null
  document_filename: string | null
  conditions: string | null
}

interface SearchResp {
  query: string
  interpretation: Interpretation | null
  results: Row[]
  total: number
  suggestions: any[]
  groups?: { category: string; count: number; rows: Row[] }[]
  cheapest?: Row | null
}

interface Facets {
  categories: { value: string; count: number }[]
  cities: { value: string; count: number }[]
  years: { value: number; count: number }[]
  providers: { id: string; name: string; city: string | null }[]
}

const EXAMPLES = ['CBC', 'C.B.C', 'صورة دم كاملة', 'MRI Brain', 'رنين مغناطيسي على المخ', 'CT Abdomen', 'HbA1c', 'Urine Analysis']

export default function SearchPage() {
  const { t, lang } = useI18n()
  const [q, setQ] = useState('')
  const [data, setData] = useState<SearchResp | null>(null)
  const [facets, setFacets] = useState<Facets | null>(null)
  const [cats, setCats] = useState<{ category: string; count: number }[]>([])
  const [busy, setBusy] = useState(false)
  const [showSug, setShowSug] = useState(false)
  const [sugs, setSugs] = useState<any[]>([])
  const [group, setGroup] = useState(true)
  const [filters, setFilters] = useState({
    category: '',
    city: '',
    provider_id: '',
    price_category: '',
    year: '',
  })
  const [priceCats, setPriceCats] = useState<{ category: string; count: number }[]>([])
  const timer = useRef<any>(null)

  useEffect(() => {
    api.get<Facets>('/search/filters').then(setFacets).catch(() => {})
    api.get<any>('/stats/categories').then(r => setCats(r.examinations)).catch(() => {})
    api.get<any[]>('/search/price-categories').then(setPriceCats).catch(() => {})
  }, [])

  async function doSearch(term: string, f = filters, grp = group) {
    if (!term.trim()) return
    setBusy(true)
    setShowSug(false)
    try {
      const p = new URLSearchParams({ q: term, group: String(grp), limit: '100' })
      if (f.category) p.set('category', f.category)
      if (f.city) p.set('city', f.city)
      if (f.provider_id) p.set('provider_id', f.provider_id)
      if (f.price_category) p.set('price_category', f.price_category)
      if (f.year) p.set('year', f.year)
      const r = await api.get<SearchResp>('/search?' + p.toString())
      setData(r)
    } catch (e) {
      console.error(e)
    } finally {
      setBusy(false)
    }
  }

  function onInput(v: string) {
    setQ(v)
    if (timer.current) clearTimeout(timer.current)
    if (v.trim().length >= 2) {
      timer.current = setTimeout(async () => {
        try {
          const r = await api.get<any[]>('/search/suggest?q=' + encodeURIComponent(v) + '&limit=8')
          setSugs(r)
          setShowSug(r.length > 0)
        } catch {}
      }, 180)
    } else {
      setShowSug(false)
    }
  }

  function applyFilter(k: string, v: string) {
    const nf = { ...filters, [k]: v }
    setFilters(nf)
    if (q.trim()) doSearch(q, nf)
  }

  const rows = data?.results ?? []
  const groups = data?.groups

  return (
    <div className="page">
      <div className="hero">
        <h1>{lang === 'ar' ? 'قارن أسعار الفحوص الطبية' : 'Compare medical examination prices'}</h1>
        <p>{t('tagline')}</p>
      </div>

      <div className="search-box">
        <input
          value={q}
          onChange={e => onInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && doSearch(q)}
          placeholder={t('searchPlaceholder')}
          autoFocus
        />
        <button className="btn" onClick={() => doSearch(q)} disabled={busy}>
          {t('search')}
        </button>
      </div>

      {showSug && (
        <div className="suggest">
          {sugs.map((s: any) => (
            <div key={s.id} onClick={() => { setQ(s.name_en || s.name_ar || ''); doSearch(s.name_en || s.name_ar || ''); }}>
              <span>{lang === 'ar' ? s.name_ar || s.name_en : s.name_en}</span>
              <span className="muted">{s.code} · {s.category}</span>
            </div>
          ))}
        </div>
      )}

      <div className="chips">
        <span className="muted">{t('tryExamples')}:</span>
        {EXAMPLES.map(x => (
          <span key={x} className="chip" onClick={() => { setQ(x); doSearch(x) }}>{x}</span>
        ))}
      </div>

      <div className="card">
        <div className="filters">
          <select value={filters.category} onChange={e => applyFilter('category', e.target.value)}>
            <option value="">{t('allCategories')}</option>
            {(facets?.categories || cats).map(c => (
              <option key={c.value} value={c.value}>{c.value} ({c.count})</option>
            ))}
          </select>
          <select value={filters.city} onChange={e => applyFilter('city', e.target.value)}>
            <option value="">{t('allCities')}</option>
            {facets?.cities.map(c => <option key={c.value} value={c.value}>{c.value}</option>)}
          </select>
          <select value={filters.provider_id} onChange={e => applyFilter('provider_id', e.target.value)}>
            <option value="">{t('allProviders')}</option>
            {facets?.providers.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <select value={filters.price_category} onChange={e => applyFilter('price_category', e.target.value)}>
            <option value="">{t('allPriceCats')}</option>
            {priceCats.map(c => <option key={c.category} value={c.category}>{c.category} ({c.count})</option>)}
          </select>
          <select value={filters.year} onChange={e => applyFilter('year', e.target.value)}>
            <option value="">{t('allYears')}</option>
            {facets?.years.map(y => <option key={y.value} value={y.value}>{y.value}</option>)}
          </select>
          <label className="row" style={{ gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={group} onChange={e => { setGroup(e.target.checked); if (q) doSearch(q, filters, e.target.checked) }} />
            {lang === 'ar' ? 'تجميع حسب فئة السعر' : 'Group by price category'}
          </label>
        </div>
      </div>

      {busy && <div className="empty">{t('loading')}</div>}

      {data && !busy && (
        <>
          {data.interpretation && (
            <div className="card">
              <h2>{t('interpretation')}</h2>
              <div className="kv">
                <div className="item"><div className="k">{lang === 'ar' ? 'مصطلح البحث' : 'User term'}</div><div className="v">{data.interpretation.user_term}</div></div>
                <div className="item"><div className="k">Normalized</div><div className="v mono">{data.interpretation.normalized}</div></div>
                <div className="item"><div className="k">{lang === 'ar' ? 'الفحص القياسي' : 'Canonical'}</div><div className="v">{lang === 'ar' ? data.interpretation.canonical_ar : data.interpretation.canonical_en}</div></div>
                <div className="item"><div className="k">Code</div><div className="v mono">{data.interpretation.canonical_code}</div></div>
                <div className="item"><div className="k">{t('category')}</div><div className="v">{data.interpretation.category}</div></div>
                <div className="item"><div className="k">{t('confidence')}</div><div className="v">{(data.interpretation.confidence * 100).toFixed(0)}% · {data.interpretation.strategy}</div></div>
              </div>
              {data.interpretation.aliases.length > 0 && (
                <>
                  <h3 style={{ marginTop: 14 }}>{t('crossRef')}</h3>
                  <div className="chips" style={{ justifyContent: 'flex-start' }}>
                    {data.interpretation.aliases.map((a, i) => (
                      <span key={i} className="chip" style={{ cursor: 'default' }}>
                        {a.alias} <span className="muted">({a.language}/{a.status})</span>
                      </span>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {!data.interpretation && data.suggestions?.length > 0 && (
            <div className="card">
              <h2>{t('suggestions')}</h2>
              <div className="chips" style={{ justifyContent: 'flex-start' }}>
                {data.suggestions.map((s: any) => (
                  <span key={s.id} className="chip" onClick={() => { setQ(s.name_en); doSearch(s.name_en) }}>
                    {s.name_en} {s.name_ar ? `· ${s.name_ar}` : ''} ({(s.score * 100).toFixed(0)}%)
                  </span>
                ))}
              </div>
            </div>
          )}

          {data.cheapest && (
            <div className="card">
              <div className="row between">
                <h2 style={{ margin: 0 }}>{t('cheapest')}</h2>
                <div className="row">
                  <span className="badge ok">{data.cheapest.price} {data.cheapest.currency}</span>
                  <span>{data.cheapest.provider_name || t('providerUnspecified')}</span>
                  <span className="muted">{data.cheapest.original_name}</span>
                </div>
              </div>
            </div>
          )}

          {groups ? (
            groups.map(g => (
              <div className="card" key={g.category}>
                <h2>
                  {t('priceTable')} · <span className="badge info">{g.category}</span>
                  <span className="muted"> {g.count} {t('records')}</span>
                </h2>
                <PriceTable rows={g.rows} />
              </div>
            ))
          ) : (
            <div className="card">
              <h2>{t('priceTable')} <span className="muted">{data.total} {t('records')}</span></h2>
              <PriceTable rows={rows} />
            </div>
          )}

          {data.total === 0 && <div className="empty">{t('noResults')}</div>}
        </>
      )}
    </div>
  )
}

function PriceTable({ rows }: { rows: Row[] }) {
  const { t, lang } = useI18n()
  if (!rows.length) return <div className="empty">{t('noResults')}</div>
  return (
    <div className="scroll-x">
      <table className="tbl">
        <thead>
          <tr>
            <th>{t('rank')}</th>
            <th>{t('center')}</th>
            <th>{t('examName')}</th>
            <th>{t('code')}</th>
            <th>{t('price')}</th>
            <th>{t('category')}</th>
            <th>{t('year')}</th>
            <th>{t('currency')}</th>
            <th>{t('city')}</th>
            <th>{t('effective')}</th>
            <th>{t('source')}</th>
            <th>{t('conditions')}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.price_value_id || i}>
              <td>{i + 1}</td>
              <td>
                {r.provider_unspecified ? (
                  <span className="badge warn">{t('providerUnspecified')}</span>
                ) : (
                  <div>
                    <div>{lang === 'ar' && r.provider_name_ar ? r.provider_name_ar : r.provider_name}</div>
                    {r.branch && <div className="muted">{r.branch}</div>}
                  </div>
                )}
              </td>
              <td>
                <div>{r.original_name}</div>
                <div className="muted">{r.exam_code}</div>
              </td>
              <td className="mono">{r.original_code}</td>
              <td className="num price">{r.price ?? '—'}</td>
              <td><span className="badge gray">{r.price_category}</span></td>
              <td>{r.tariff_year ?? '—'}</td>
              <td>{r.currency}</td>
              <td>{r.city || r.governorate || '—'}</td>
              <td>{r.effective_date || '—'}</td>
              <td>
                <div className="muted">{r.document_filename || r.document_title}</div>
                {r.source_page && <div className="muted">{t('page')} {r.source_page}{r.source_sheet ? ` · ${r.source_sheet}` : ''}</div>}
              </td>
              <td className="muted" style={{ maxWidth: 180 }}>{r.conditions || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

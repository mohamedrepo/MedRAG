const BASE = '/api/v1'

let token: string | null = localStorage.getItem('medrag.token')

export function setToken(t: string | null) {
  token = t
  if (t) localStorage.setItem('medrag.token', t)
  else localStorage.removeItem('medrag.token')
}

export function getToken() {
  return token
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  }
  if (token) headers.Authorization = `Bearer ${token}`
  if (init.body && !(init.body instanceof FormData) && typeof init.body === 'string') {
    headers['Content-Type'] = 'application/json'
  } else if (init.body && !(init.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
    init = { ...init, body: JSON.stringify(init.body) }
  }
  const res = await fetch(BASE + path, { ...init, headers })
  if (res.status === 401) {
    setToken(null)
    throw new Error('401')
  }
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail ?? JSON.stringify(j)
    } catch {}
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  get: <T,>(p: string) => request<T>(p),
  post: <T,>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body as any }),
  patch: <T,>(p: string, body?: unknown) =>
    request<T>(p, { method: 'PATCH', body: body as any }),
  upload: <T,>(p: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<T>(p, { method: 'POST', body: fd })
  },
}

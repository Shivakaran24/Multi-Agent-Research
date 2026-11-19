const KEY = 'fieldwork.theme'
export type Theme = 'light' | 'dark'

export function currentTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY)
    if (saved === 'light' || saved === 'dark') return saved
  } catch { /* storage may be blocked */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(theme: Theme, remember = false) {
  document.documentElement.dataset.theme = theme
  if (remember) { try { localStorage.setItem(KEY, theme) } catch { /* ignore */ } }
}

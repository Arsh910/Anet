import { useEffect, useState } from 'react'
import { Moon, Sun } from 'lucide-react'

/**
 * ThemeToggle - Dark/Light mode toggle
 *
 * Features:
 * - Toggle between dark and light themes
 * - Persists preference to localStorage
 * - Smooth transitions
 * - Respects system theme preference
 * - Accessible button with ARIA labels
 *
 * Props: None
 */
export default function ThemeToggle() {
  const [theme, setTheme] = useState('dark')
  const [mounted, setMounted] = useState(false)

  // Initialize theme on mount
  useEffect(() => {
    // Check for stored theme preference
    const storedTheme = localStorage.getItem('theme-preference')

    if (storedTheme) {
      setTheme(storedTheme)
      applyTheme(storedTheme)
    } else {
      // Check system preference
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
      const initialTheme = prefersDark ? 'dark' : 'light'
      setTheme(initialTheme)
      applyTheme(initialTheme)
    }

    setMounted(true)
  }, [])

  // Apply theme to DOM
  const applyTheme = (themeName) => {
    document.documentElement.setAttribute('data-theme', themeName)
    localStorage.setItem('theme-preference', themeName)
  }

  // Toggle theme
  const toggleTheme = () => {
    const newTheme = theme === 'dark' ? 'light' : 'dark'
    setTheme(newTheme)
    applyTheme(newTheme)
  }

  if (!mounted) return null

  return (
    <button
      onClick={toggleTheme}
      className="icon-btn"
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
      style={{
        position: 'relative',
      }}
    >
      {theme === 'dark' ? (
        <Sun size={16} />
      ) : (
        <Moon size={16} />
      )}
    </button>
  )
}

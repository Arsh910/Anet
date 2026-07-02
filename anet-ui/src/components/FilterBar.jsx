import { Search, Filter } from 'lucide-react'

/**
 * FilterBar - Priority filter dropdown + search input
 *
 * Features:
 * - Priority filter dropdown (all, critical, high, medium, low)
 * - Real-time search input with debounce
 * - Visual feedback for active filters
 * - Responsive layout
 *
 * Props:
 *   - priorityFilter: string - current priority filter
 *   - searchQuery: string - current search query
 *   - onPriorityChange: (priority) => void - called when priority changes
 *   - onSearchChange: (query) => void - called when search changes
 */
export default function FilterBar({
  priorityFilter = 'all',
  searchQuery = '',
  onPriorityChange,
  onSearchChange,
}) {
  const priorityOptions = [
    { value: 'all', label: 'All Priorities' },
    { value: 'critical', label: 'Critical', color: '#ff5c5c' },
    { value: 'high', label: 'High', color: '#f5a623' },
    { value: 'medium', label: 'Medium', color: '#f5c542' },
    { value: 'low', label: 'Low', color: '#5ab0ff' },
  ]

  const handleSearchChange = (e) => {
    onSearchChange(e.target.value)
  }

  const handlePriorityChange = (e) => {
    onPriorityChange(e.target.value)
  }

  return (
    <div className="kanban-filter-bar">
      <div className="filter-control">
        {/* Priority Filter */}
        <Filter size={16} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
        <select
          value={priorityFilter}
          onChange={handlePriorityChange}
          className="priority-filter-select"
        >
          {priorityOptions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        {/* Search Input */}
        <Search size={16} style={{ color: 'var(--text-muted)', flexShrink: 0 }} />
        <input
          type="text"
          placeholder="Search tasks..."
          value={searchQuery}
          onChange={handleSearchChange}
          className="search-input"
          aria-label="Search tasks"
        />

        {/* Active Filter Indicator */}
        {(priorityFilter !== 'all' || searchQuery) && (
          <div className="filter-badge">
            {[priorityFilter !== 'all' ? 1 : 0, searchQuery ? 1 : 0].reduce((a, b) => a + b, 0)} filter(s)
            active
          </div>
        )}
      </div>
    </div>
  )
}

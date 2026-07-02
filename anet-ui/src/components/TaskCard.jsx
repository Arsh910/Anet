import { Edit2, Trash2, Calendar, AlertCircle } from 'lucide-react'
import { useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

/**
 * TaskCard - Individual task card with drag-drop support
 *
 * Features:
 * - Title display
 * - Priority badge (critical, high, medium, low) with color coding
 * - Due date display with overdue indicator
 * - Description snippet
 * - Tag chip display
 * - Edit/delete buttons
 * - Drag-drop handle (via useSortable)
 * - Overdue visual indicator (red styling)
 *
 * Props:
 *   - task: object - task data { id, title, priority, dueDate, description, tags }
 *   - onEdit: (task) => void - edit callback
 *   - onDelete: (taskId) => void - delete callback
 */
export default function TaskCard({ task, onEdit, onDelete }) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({
    id: task.id,
    data: { type: 'Task', task },
  })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  }

  // Check if due date is in the past
  const isOverdue = () => {
    if (!task.dueDate) return false
    const dueDate = new Date(task.dueDate)
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    dueDate.setHours(0, 0, 0, 0)
    return dueDate < today
  }

  const overdue = isOverdue()

  // Format date
  const formatDate = (dateStr) => {
    if (!dateStr) return null
    const date = new Date(dateStr)
    const today = new Date()
    const tomorrow = new Date(today)
    tomorrow.setDate(tomorrow.getDate() + 1)

    // Same day
    if (date.toDateString() === today.toDateString()) {
      return 'Today'
    }
    // Next day
    if (date.toDateString() === tomorrow.toDateString()) {
      return 'Tomorrow'
    }

    // Format as short date
    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    })
  }

  // Priority colors and labels
  const priorityConfig = {
    critical: { color: '#ff5c5c', label: 'Critical', bg: 'rgba(255, 92, 92, 0.15)' },
    high: { color: '#f5a623', label: 'High', bg: 'rgba(245, 166, 35, 0.15)' },
    medium: { color: '#f5c542', label: 'Medium', bg: 'rgba(245, 197, 66, 0.15)' },
    low: { color: '#5ab0ff', label: 'Low', bg: 'rgba(90, 176, 255, 0.15)' },
  }

  const priority = priorityConfig[task.priority] || priorityConfig.medium

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`task-card ${overdue ? 'overdue' : ''}`}
      {...attributes}
      {...listeners}
      role="article"
      aria-label={`Task: ${task.title} - ${overdue ? 'Overdue' : ''}`}
    >
      {/* Header with priority and drag handle */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          gap: '8px',
        }}
      >
        {/* Priority Badge */}
        <div className={`priority-badge ${task.priority || 'medium'}`}>
          {priority.label}
        </div>

        {/* Overdue Indicator */}
        {overdue && (
          <AlertCircle
            size={14}
            className="overdue-indicator"
            title="Overdue"
            aria-label="Task is overdue"
          />
        )}
      </div>

      {/* Title */}
      <div className="task-title">{task.title}</div>

      {/* Description snippet */}
      {task.description && <div className="task-description">{task.description}</div>}

      {/* Due Date */}
      {task.dueDate && (
        <div className="task-due-date">
          <Calendar size={13} />
          <span>{formatDate(task.dueDate)}</span>
        </div>
      )}

      {/* Tags / Chips */}
      {task.tags && task.tags.length > 0 && (
        <div
          style={{
            display: 'flex',
            gap: '6px',
            flexWrap: 'wrap',
            marginTop: '4px',
          }}
        >
          {task.tags.map((tag) => (
            <div
              key={tag}
              className="chip"
              style={{
                fontSize: '9.5px',
                maxWidth: '100%',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              title={tag}
            >
              {tag}
            </div>
          ))}
        </div>
      )}

      {/* Action Buttons */}
      <div className="task-actions">
        <button
          className="icon-btn"
          onClick={() => onEdit(task)}
          title="Edit task"
          aria-label={`Edit ${task.title}`}
        >
          <Edit2 size={14} />
        </button>
        <button
          className="icon-btn"
          onClick={() => onDelete(task.id)}
          title="Delete task"
          aria-label={`Delete ${task.title}`}
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  )
}

// Styled task card wrapper
export const TaskCardWrapper = ({ children }) => (
  <div
    style={{
      padding: '10px 12px',
      borderRadius: 'var(--radius-md)',
      backgroundColor: 'var(--card)',
      border: '1px solid var(--card-border)',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      cursor: 'grab',
      transition: 'all 0.2s',
      userSelect: 'none',
    }}
  >
    {children}
  </div>
)

import { useEffect, useRef, useState } from 'react'
import { X, Calendar } from 'lucide-react'

/**
 * TaskModal - Add/Edit task form with validation
 *
 * Features:
 * - Add or edit mode based on task prop
 * - Title required validation
 * - Due date cannot be in the past
 * - Focus trap (focus stays in modal)
 * - Escape key closes modal
 * - Keyboard navigation (Tab, Shift+Tab)
 *
 * Props:
 *   - isOpen: boolean - whether modal is visible
 *   - task: object | null - task to edit (null for new task)
 *   - columnId: string - column to add task to (for new tasks)
 *   - onSave: (columnId, task) => void - save callback
 *   - onClose: () => void - close callback
 */
export default function TaskModal({ isOpen, task, columnId, onSave, onClose }) {
  const modalRef = useRef(null)
  const firstFocusRef = useRef(null)
  const lastFocusRef = useRef(null)

  const [formData, setFormData] = useState({
    title: '',
    description: '',
    priority: 'medium',
    dueDate: '',
  })

  const [errors, setErrors] = useState({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Initialize form with task data or defaults
  useEffect(() => {
    if (isOpen) {
      if (task) {
        setFormData({
          title: task.title || '',
          description: task.description || '',
          priority: task.priority || 'medium',
          dueDate: task.dueDate || '',
        })
      } else {
        // New task - set reasonable default due date
        const tomorrow = new Date()
        tomorrow.setDate(tomorrow.getDate() + 1)
        setFormData({
          title: '',
          description: '',
          priority: 'medium',
          dueDate: tomorrow.toISOString().split('T')[0],
        })
      }
      setErrors({})
      // Focus first input after render
      setTimeout(() => firstFocusRef.current?.focus(), 0)
    }
  }, [isOpen, task])

  // Handle Escape key to close
  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape' && isOpen) {
        onClose()
      }
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  // Focus trap - keep focus within modal
  useEffect(() => {
    if (!isOpen) return

    const handleKeyDown = (e) => {
      if (e.key !== 'Tab') return

      const activeElement = document.activeElement
      const isFirstElement = activeElement === firstFocusRef.current
      const isLastElement = activeElement === lastFocusRef.current

      if (e.shiftKey) {
        // Shift+Tab on first element → focus last
        if (isFirstElement) {
          e.preventDefault()
          lastFocusRef.current?.focus()
        }
      } else {
        // Tab on last element → focus first
        if (isLastElement) {
          e.preventDefault()
          firstFocusRef.current?.focus()
        }
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen])

  // Validate form
  const validate = () => {
    const newErrors = {}

    if (!formData.title.trim()) {
      newErrors.title = 'Title is required'
    }

    if (formData.dueDate) {
      const dueDate = new Date(formData.dueDate)
      const today = new Date()
      today.setHours(0, 0, 0, 0)

      if (dueDate < today) {
        newErrors.dueDate = 'Due date cannot be in the past'
      }
    }

    setErrors(newErrors)
    return Object.keys(newErrors).length === 0
  }

  // Handle form submission
  const handleSubmit = async (e) => {
    e.preventDefault()

    if (!validate()) {
      return
    }

    setIsSubmitting(true)

    try {
      // Simulate slight delay for better UX
      await new Promise((resolve) => setTimeout(resolve, 300))

      const taskData = {
        id: task?.id,
        title: formData.title.trim(),
        description: formData.description.trim(),
        priority: formData.priority,
        dueDate: formData.dueDate,
      }

      if (task) {
        // Edit mode - don't pass columnId
        onSave(taskData)
      } else {
        // Add mode - pass columnId
        onSave(columnId, taskData)
      }

      onClose()
    } finally {
      setIsSubmitting(false)
    }
  }

  // Handle input changes
  const handleChange = (e) => {
    const { name, value } = e.target
    setFormData((prev) => ({ ...prev, [name]: value }))
    // Clear error for this field on change
    if (errors[name]) {
      setErrors((prev) => {
        const newErrors = { ...prev }
        delete newErrors[name]
        return newErrors
      })
    }
  }

  if (!isOpen) return null

  return (
    <div className="kanban-modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="kanban-modal" ref={modalRef} role="dialog" aria-modal="true">
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h3>{task ? 'Edit Task' : 'Add New Task'}</h3>
          <button
            ref={lastFocusRef}
            className="icon-btn"
            onClick={onClose}
            aria-label="Close modal"
            title="Close (Esc)"
          >
            <X size={18} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit}>
          {/* Title Field */}
          <div className="form-field">
            <label htmlFor="task-title" className="form-label">
              Title *
            </label>
            <input
              ref={firstFocusRef}
              id="task-title"
              type="text"
              name="title"
              className="form-input"
              placeholder="Enter task title"
              value={formData.title}
              onChange={handleChange}
              disabled={isSubmitting}
              autoComplete="off"
              aria-invalid={Boolean(errors.title)}
              aria-describedby={errors.title ? 'title-error' : undefined}
            />
            {errors.title && (
              <div id="title-error" className="form-error">
                <span>●</span> {errors.title}
              </div>
            )}
          </div>

          {/* Description Field */}
          <div className="form-field">
            <label htmlFor="task-description" className="form-label">
              Description
            </label>
            <textarea
              id="task-description"
              name="description"
              className="form-textarea"
              placeholder="Add task details (optional)"
              value={formData.description}
              onChange={handleChange}
              disabled={isSubmitting}
            />
          </div>

          {/* Priority Field */}
          <div className="form-field">
            <label htmlFor="task-priority" className="form-label">
              Priority
            </label>
            <select
              id="task-priority"
              name="priority"
              className="form-select"
              value={formData.priority}
              onChange={handleChange}
              disabled={isSubmitting}
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
          </div>

          {/* Due Date Field */}
          <div className="form-field">
            <label htmlFor="task-due-date" className="form-label">
              Due Date
            </label>
            <div style={{ position: 'relative', display: 'flex', alignItems: 'center' }}>
              <input
                id="task-due-date"
                type="date"
                name="dueDate"
                className="form-input"
                value={formData.dueDate}
                onChange={handleChange}
                disabled={isSubmitting}
                aria-invalid={Boolean(errors.dueDate)}
                aria-describedby={errors.dueDate ? 'due-date-error' : undefined}
                style={{ paddingRight: '32px' }}
              />
              <Calendar
                size={16}
                style={{ position: 'absolute', right: '10px', pointerEvents: 'none', color: 'var(--text-muted)' }}
              />
            </div>
            {errors.dueDate && (
              <div id="due-date-error" className="form-error">
                <span>●</span> {errors.dueDate}
              </div>
            )}
          </div>

          {/* Action Buttons */}
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-outline"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={isSubmitting}
              aria-busy={isSubmitting}
            >
              {isSubmitting ? 'Saving...' : 'Save Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

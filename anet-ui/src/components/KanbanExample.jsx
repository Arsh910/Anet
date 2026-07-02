import React, { useState } from 'react'
import { useKanbanStore } from '../store/useKanbanStore'

/**
 * Example Kanban Board Component
 * 
 * Demonstrates:
 * - Loading tasks and columns from store
 * - Filtering by priority
 * - Searching tasks
 * - Adding, updating, deleting tasks
 * - Moving tasks between columns
 */
export default function KanbanExample() {
  const {
    // State
    columns,
    columnOrder,
    tasks,
    priorityFilter,
    searchQuery,

    // Filters
    setPriorityFilter,
    setSearchQuery,
    getFilteredTasks,

    // Task actions
    addTask,
    updateTask,
    deleteTask,
    moveTask,
    reorderTasks,

    // Column actions
    addColumn,
    updateColumn,
    deleteColumn,

    // Utilities
    resetToSampleData,
    clearAll,
    exportState,
    importState,
  } = useKanbanStore()

  const [newTaskForm, setNewTaskForm] = useState({
    columnId: 'col-todo',
    title: '',
    description: '',
    priority: 'medium',
    dueDate: '',
  })

  const [newColumnForm, setNewColumnForm] = useState({
    title: '',
  })

  // Get filtered tasks
  const filteredTasks = getFilteredTasks()
  const isFiltered = priorityFilter !== 'all' || searchQuery.trim()

  // ═════════════════════════════════════════════════════════════════
  // HANDLERS
  // ═════════════════════════════════════════════════════════════════

  const handleAddTask = () => {
    if (!newTaskForm.title.trim()) return

    addTask(newTaskForm.columnId, {
      title: newTaskForm.title,
      description: newTaskForm.description,
      priority: newTaskForm.priority,
      dueDate: newTaskForm.dueDate,
    })

    setNewTaskForm({
      columnId: 'col-todo',
      title: '',
      description: '',
      priority: 'medium',
      dueDate: '',
    })
  }

  const handleAddColumn = () => {
    if (!newColumnForm.title.trim()) return

    addColumn({
      title: newColumnForm.title,
      taskIds: [],
    })

    setNewColumnForm({ title: '' })
  }

  const handleDeleteTask = (taskId) => {
    if (confirm('Delete this task?')) {
      deleteTask(taskId)
    }
  }

  const handleDeleteColumn = (columnId) => {
    if (confirm('Delete this column? Tasks will remain.')) {
      deleteColumn(columnId)
    }
  }

  const handleExport = () => {
    const exported = exportState()
    const json = JSON.stringify(exported, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `kanban-export-${new Date().toISOString().split('T')[0]}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  // ═════════════════════════════════════════════════════════════════
  // RENDER
  // ═════════════════════════════════════════════════════════════════

  return (
    <div className="kanban-example">
      <div className="kanban-header">
        <h1>📋 Kanban Board</h1>
        <div className="kanban-actions">
          <button onClick={resetToSampleData} title="Reset to 6 sample tasks">
            ↻ Reset
          </button>
          <button onClick={clearAll} title="Clear all tasks and columns">
            🗑️ Clear All
          </button>
          <button onClick={handleExport} title="Download board as JSON">
            ⬇️ Export
          </button>
        </div>
      </div>

      {/* FILTERS & SEARCH */}
      <div className="kanban-filters">
        <div className="filter-group">
          <label>Priority Filter:</label>
          <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
            <option value="all">All</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </div>

        <div className="filter-group">
          <label>Search:</label>
          <input
            type="text"
            placeholder="Search tasks..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        {isFiltered && (
          <div className="filter-status">
            <p>
              Showing <strong>{filteredTasks.length}</strong> of{' '}
              <strong>{Object.keys(tasks).length}</strong> tasks
            </p>
            <button onClick={() => { setPriorityFilter('all'); setSearchQuery('') }}>
              Clear Filters
            </button>
          </div>
        )}
      </div>

      {/* ADD NEW TASK FORM */}
      <div className="add-task-form">
        <h3>➕ Add Task</h3>
        <div className="form-row">
          <input
            type="text"
            placeholder="Task title"
            value={newTaskForm.title}
            onChange={(e) => setNewTaskForm({ ...newTaskForm, title: e.target.value })}
          />
          <input
            type="text"
            placeholder="Description"
            value={newTaskForm.description}
            onChange={(e) => setNewTaskForm({ ...newTaskForm, description: e.target.value })}
          />
          <select
            value={newTaskForm.columnId}
            onChange={(e) => setNewTaskForm({ ...newTaskForm, columnId: e.target.value })}
          >
            {columnOrder.map((colId) => (
              <option key={colId} value={colId}>
                {columns[colId].title}
              </option>
            ))}
          </select>
          <select
            value={newTaskForm.priority}
            onChange={(e) => setNewTaskForm({ ...newTaskForm, priority: e.target.value })}
          >
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </select>
          <input
            type="date"
            value={newTaskForm.dueDate}
            onChange={(e) => setNewTaskForm({ ...newTaskForm, dueDate: e.target.value })}
          />
          <button onClick={handleAddTask}>Add</button>
        </div>
      </div>

      {/* ADD NEW COLUMN FORM */}
      <div className="add-column-form">
        <h3>➕ Add Column</h3>
        <div className="form-row">
          <input
            type="text"
            placeholder="Column title"
            value={newColumnForm.title}
            onChange={(e) => setNewColumnForm({ ...newColumnForm, title: e.target.value })}
          />
          <button onClick={handleAddColumn}>Add Column</button>
        </div>
      </div>

      {/* BOARD VIEW */}
      {isFiltered ? (
        // Filtered view - show all matching tasks in a list
        <div className="filtered-view">
          <h3>🔍 Filtered Results ({filteredTasks.length})</h3>
          <div className="task-list">
            {filteredTasks.map((task) => {
              const taskColumn = Object.values(columns).find((col) =>
                col.taskIds.includes(task.id)
              )
              return (
                <div key={task.id} className={`task-item priority-${task.priority}`}>
                  <div className="task-header">
                    <h4>{task.title}</h4>
                    <span className="badge">{taskColumn?.title || 'Unknown'}</span>
                  </div>
                  {task.description && <p>{task.description}</p>}
                  <div className="task-meta">
                    <span className={`priority priority-${task.priority}`}>{task.priority}</span>
                    {task.dueDate && <span className="due-date">{task.dueDate}</span>}
                  </div>
                  <button
                    className="btn-delete"
                    onClick={() => handleDeleteTask(task.id)}
                    title="Delete task"
                  >
                    ✕
                  </button>
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        // Normal board view
        <div className="board">
          {columnOrder.map((colId) => {
            const column = columns[colId]
            const columnTasks = column.taskIds.map((taskId) => tasks[taskId]).filter(Boolean)

            return (
              <div key={colId} className="column">
                <div className="column-header">
                  <h2>{column.title}</h2>
                  <span className="task-count">{columnTasks.length}</span>
                  <button
                    className="btn-delete-column"
                    onClick={() => handleDeleteColumn(colId)}
                    title="Delete column"
                  >
                    ✕
                  </button>
                </div>

                <div className="tasks">
                  {columnTasks.map((task) => (
                    <div key={task.id} className={`task priority-${task.priority}`}>
                      <div className="task-content">
                        <h4>{task.title}</h4>
                        {task.description && <p>{task.description}</p>}
                      </div>
                      <div className="task-footer">
                        <span className={`badge priority-${task.priority}`}>{task.priority}</span>
                        {task.dueDate && <span className="due-date">{task.dueDate}</span>}
                      </div>
                      <button
                        className="btn-delete-task"
                        onClick={() => handleDeleteTask(task.id)}
                        title="Delete task"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* STATE DEBUG INFO */}
      <div className="debug-info">
        <summary>📊 State Info</summary>
        <details>
          <p>
            <strong>Tasks:</strong> {Object.keys(tasks).length}
          </p>
          <p>
            <strong>Columns:</strong> {columnOrder.length}
          </p>
          <p>
            <strong>Priority Filter:</strong> {priorityFilter}
          </p>
          <p>
            <strong>Search Query:</strong> {searchQuery || '(empty)'}
          </p>
          <p>
            <strong>Filtered Tasks:</strong> {filteredTasks.length}
          </p>
          <code>{JSON.stringify({ tasks: Object.keys(tasks).length, columns: columnOrder.length }, null, 2)}</code>
        </details>
      </div>
    </div>
  )
}

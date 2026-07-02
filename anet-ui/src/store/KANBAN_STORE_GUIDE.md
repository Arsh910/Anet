# Kanban Store Guide

## Overview

`useKanbanStore` is a Zustand-based state management hook for a Kanban board application with full localStorage persistence and filtering capabilities.

## Features

✅ **Persistent State**: Automatically syncs to localStorage on every change  
✅ **6 Sample Tasks**: Seeded on first load with realistic project tasks  
✅ **3 Default Columns**: "To Do", "In Progress", "Done"  
✅ **Task Management**: Add, update, delete, and move tasks between columns  
✅ **Column Management**: Add, rename, delete, and reorder columns  
✅ **Priority Filtering**: Filter tasks by priority level  
✅ **Search**: Full-text search across task titles and descriptions  
✅ **Export/Import**: Backup and restore board state  

---

## Usage Examples

### Basic Setup

```jsx
import { useKanbanStore } from './store/useKanbanStore'

function KanbanBoard() {
  const { columns, columnOrder, tasks } = useKanbanStore()

  return (
    <div className="kanban">
      {columnOrder.map(colId => {
        const column = columns[colId]
        return (
          <div key={colId} className="column">
            <h2>{column.title}</h2>
            {column.taskIds.map(taskId => (
              <div key={taskId} className="task">
                {tasks[taskId].title}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}
```

### Adding Tasks

```jsx
const { addTask } = useKanbanStore()

// Add to "To Do" column
addTask('col-todo', {
  title: 'New feature request',
  description: 'Add dark mode support',
  priority: 'medium',
  dueDate: '2026-07-15',
})
```

### Moving Tasks

```jsx
const { moveTask } = useKanbanStore()

// Move task from "To Do" to "In Progress"
moveTask('task-1', 'col-todo', 'col-in-progress', 0)
// The last parameter is the destination index
```

### Updating Tasks

```jsx
const { updateTask } = useKanbanStore()

updateTask('task-1', {
  priority: 'high',
  dueDate: '2026-07-08',
})
```

### Filtering by Priority

```jsx
function TaskFilter() {
  const { priorityFilter, setPriorityFilter, getFilteredTasks } = useKanbanStore()

  const filteredTasks = getFilteredTasks()

  return (
    <div>
      <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
        <option value="all">All</option>
        <option value="critical">Critical</option>
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
      <p>Showing {filteredTasks.length} tasks</p>
    </div>
  )
}
```

### Search

```jsx
function SearchTasks() {
  const { searchQuery, setSearchQuery, getFilteredTasks } = useKanbanStore()

  const results = getFilteredTasks()

  return (
    <div>
      <input
        type="text"
        placeholder="Search tasks..."
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
      />
      <p>Found {results.length} results</p>
      {results.map(task => (
        <div key={task.id}>{task.title}</div>
      ))}
    </div>
  )
}
```

### Reordering Tasks

```jsx
const { reorderTasks } = useKanbanStore()

// Reorder tasks in a column
reorderTasks('col-todo', ['task-3', 'task-1', 'task-6'])
```

### Column Management

```jsx
const { addColumn, updateColumn, deleteColumn } = useKanbanStore()

// Add a new column
addColumn({
  title: 'On Hold',
  taskIds: [],
})

// Rename a column
updateColumn('col-todo', { title: 'Backlog' })

// Delete a column
deleteColumn('col-todo')
```

---

## State Structure

### Tasks Map

```javascript
{
  'task-1': {
    id: 'task-1',
    title: 'Design new landing page',
    description: 'Create mockups and wireframes...',
    priority: 'high',        // 'critical', 'high', 'medium', 'low'
    dueDate: '2026-07-10',
    createdAt: '2026-07-02T10:00:00Z',
  },
  // ... more tasks
}
```

### Columns Map

```javascript
{
  'col-todo': {
    id: 'col-todo',
    title: 'To Do',
    taskIds: ['task-1', 'task-3', 'task-6'],
  },
  'col-in-progress': {
    id: 'col-in-progress',
    title: 'In Progress',
    taskIds: ['task-2', 'task-4'],
  },
  'col-done': {
    id: 'col-done',
    title: 'Done',
    taskIds: ['task-5'],
  },
}
```

---

## API Reference

### State Properties

| Property | Type | Description |
|----------|------|-------------|
| `tasks` | Object | Map of taskId → task object |
| `columns` | Object | Map of columnId → column object |
| `columnOrder` | Array | Ordered list of columnIds |
| `priorityFilter` | String | Current priority filter ('all', 'critical', 'high', 'medium', 'low') |
| `searchQuery` | String | Current search query string |

### Task Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `addTask` | `(columnId, task) => void` | Add new task to column |
| `updateTask` | `(taskId, updates) => void` | Update task properties |
| `deleteTask` | `(taskId) => void` | Remove task from board |
| `moveTask` | `(taskId, sourceColId, destColId, destIndex) => void` | Move task to different column |
| `reorderTasks` | `(columnId, taskIds) => void` | Reorder tasks within column |

### Column Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `addColumn` | `(column) => void` | Create new column |
| `updateColumn` | `(columnId, updates) => void` | Update column properties |
| `deleteColumn` | `(columnId) => void` | Remove column |
| `reorderColumns` | `(newColumnOrder) => void` | Reorder columns |

### Filter Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `setPriorityFilter` | `(priority) => void` | Set priority filter |
| `setSearchQuery` | `(query) => void` | Set search query |
| `getFilteredTasks` | `() => Array` | Get filtered tasks (both filters applied) |

### Utility Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `resetToSampleData` | `() => void` | Reset to 6 sample tasks |
| `clearAll` | `() => void` | Remove all tasks and columns |
| `exportState` | `() => Object` | Export state as JSON |
| `importState` | `(state) => void` | Import state from JSON |

---

## Sample Tasks

The store seeds with 6 realistic tasks:

1. **Design new landing page** (High priority, To Do)
2. **Fix authentication bug** (Critical priority, In Progress)
3. **Write documentation** (Medium priority, To Do)
4. **Code review PRs** (High priority, In Progress)
5. **Optimize database queries** (Medium priority, Done)
6. **Update dependencies** (Low priority, To Do)

---

## localStorage Structure

State is persisted to `localStorage` under the key `kanban-board-state`:

```javascript
{
  "tasks": { ... },
  "columns": { ... },
  "columnOrder": ["col-todo", "col-in-progress", "col-done"]
}
```

The store automatically:
- ✅ Loads from localStorage on mount (if available)
- ✅ Saves on every state change
- ✅ Seeds sample data on first load
- ✅ Handles errors gracefully

---

## Testing

### Reset to Sample Data

```jsx
const { resetToSampleData } = useKanbanStore()
resetToSampleData()  // Restores 6 sample tasks
```

### Clear Everything

```jsx
const { clearAll } = useKanbanStore()
clearAll()  // Wipes board clean
```

### Export/Import

```jsx
const { exportState, importState } = useKanbanStore()

// Export
const backup = exportState()
localStorage.setItem('kanban-backup', JSON.stringify(backup))

// Import later
const saved = JSON.parse(localStorage.getItem('kanban-backup'))
importState(saved)
```

---

## Integration with React Components

### Full Board Example

```jsx
import { useKanbanStore } from './store/useKanbanStore'

function KanbanApp() {
  const {
    columns,
    columnOrder,
    tasks,
    priorityFilter,
    setPriorityFilter,
    searchQuery,
    setSearchQuery,
    moveTask,
    addTask,
    deleteTask,
  } = useKanbanStore()

  const handleDragEnd = (result) => {
    const { source, destination, draggableId } = result
    if (!destination) return

    const taskId = draggableId.replace('task-', '')
    moveTask(
      taskId,
      source.droppableId,
      destination.droppableId,
      destination.index
    )
  }

  return (
    <div className="kanban-app">
      <div className="controls">
        <input
          type="text"
          placeholder="Search..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
        />
        <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
          <option value="all">All</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </div>

      <div className="board">
        {columnOrder.map((colId) => {
          const column = columns[colId]
          return (
            <div key={colId} className="column">
              <h2>{column.title}</h2>
              {column.taskIds.map((taskId) => {
                const task = tasks[taskId]
                return (
                  <div key={taskId} className="card">
                    <h3>{task.title}</h3>
                    <p>{task.description}</p>
                    <span className={`priority-${task.priority}`}>{task.priority}</span>
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default KanbanApp
```

---

## Common Patterns

### Conditional Rendering Based on Filters

```jsx
const { columns, columnOrder, tasks, getFilteredTasks, priorityFilter, searchQuery } = useKanbanStore()

const filteredTasks = getFilteredTasks()

if (priorityFilter !== 'all' || searchQuery) {
  // Show filtered view across all columns
  return (
    <div className="filtered-list">
      {filteredTasks.map(task => (
        <TaskCard key={task.id} task={task} />
      ))}
    </div>
  )
}

// Otherwise show normal board
```

### Real-time Update with Debounce (for rename)

```jsx
import { useState } from 'react'

function ColumnHeader({ columnId }) {
  const { columns, updateColumn } = useKanbanStore()
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(columns[columnId].title)

  const handleSave = () => {
    updateColumn(columnId, { title })
    setEditing(false)
  }

  return (
    <div className="column-header">
      {editing ? (
        <input value={title} onChange={(e) => setTitle(e.target.value)} onBlur={handleSave} />
      ) : (
        <h2 onDoubleClick={() => setEditing(true)}>{columns[columnId].title}</h2>
      )}
    </div>
  )
}
```

---

## Troubleshooting

### Tasks Not Persisting?

Check browser DevTools → Application → localStorage for `kanban-board-state` key. If not present:
1. Check for JavaScript errors in console
2. Verify localStorage is not disabled
3. Try `resetToSampleData()` to reinitialize

### Filters Not Working?

Make sure to use `getFilteredTasks()` getter instead of accessing `tasks` directly:

```jsx
// ✅ Correct
const filtered = getFilteredTasks()

// ❌ Wrong
const filtered = Object.values(tasks)  // Ignores filters!
```

### Lost Data?

Call `exportState()` periodically and store the JSON. You can always `importState()` to recover.

---

## Next Steps

1. **Connect to React Components**: Use hooks in your Kanban board UI
2. **Drag & Drop Integration**: Use react-beautiful-dnd or react-dnd with `moveTask()`
3. **Real-time Sync**: Add WebSocket listeners to sync with server
4. **Undo/Redo**: Implement history stack with actions
5. **Collaboration**: Add user identities and conflict resolution

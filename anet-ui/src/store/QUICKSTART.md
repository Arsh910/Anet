# 🚀 Kanban Store Quick Start

Get up and running in 2 minutes!

---

## 1. Import the Store

```jsx
import { useKanbanStore } from './store/useKanbanStore'
```

---

## 2. Use in Your Component

```jsx
function MyKanbanApp() {
  const { columns, columnOrder, tasks, addTask } = useKanbanStore()

  return (
    <div className="board">
      {columnOrder.map(colId => (
        <div key={colId} className="column">
          <h2>{columns[colId].title}</h2>
          {columns[colId].taskIds.map(taskId => (
            <div key={taskId} className="task">
              <h3>{tasks[taskId].title}</h3>
              <p>{tasks[taskId].description}</p>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
```

---

## 3. Add Tasks

```jsx
const { addTask } = useKanbanStore()

// Add to "To Do" column
addTask('col-todo', {
  title: 'New task',
  description: 'Task details',
  priority: 'high',
  dueDate: '2026-07-15'
})
```

---

## 4. Move Tasks Between Columns

```jsx
const { moveTask } = useKanbanStore()

// Move task from "To Do" to "In Progress"
moveTask('task-1', 'col-todo', 'col-in-progress', 0)
```

---

## 5. Filter by Priority

```jsx
function FilteredView() {
  const { priorityFilter, setPriorityFilter, getFilteredTasks } = useKanbanStore()

  const filtered = getFilteredTasks()

  return (
    <div>
      <select value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)}>
        <option value="all">All</option>
        <option value="critical">Critical</option>
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
      <p>Showing {filtered.length} tasks</p>
    </div>
  )
}
```

---

## 6. Search Tasks

```jsx
function SearchBar() {
  const { searchQuery, setSearchQuery } = useKanbanStore()

  return (
    <input
      type="text"
      placeholder="Search..."
      value={searchQuery}
      onChange={e => setSearchQuery(e.target.value)}
    />
  )
}
```

---

## Complete Example

```jsx
import { useKanbanStore } from './store/useKanbanStore'

export default function KanbanApp() {
  const {
    // State
    columns,
    columnOrder,
    tasks,
    priorityFilter,
    searchQuery,

    // Actions
    addTask,
    deleteTask,
    moveTask,
    setPriorityFilter,
    setSearchQuery,
    getFilteredTasks,

    // Utils
    resetToSampleData,
  } = useKanbanStore()

  const [newTaskTitle, setNewTaskTitle] = useState('')

  const handleAddTask = () => {
    if (newTaskTitle.trim()) {
      addTask('col-todo', {
        title: newTaskTitle,
        priority: 'medium'
      })
      setNewTaskTitle('')
    }
  }

  const filtered = getFilteredTasks()

  return (
    <div style={{ padding: '20px' }}>
      <h1>📋 My Kanban Board</h1>

      {/* Controls */}
      <div style={{ marginBottom: '20px' }}>
        <input
          type="text"
          placeholder="New task title..."
          value={newTaskTitle}
          onChange={e => setNewTaskTitle(e.target.value)}
          onKeyPress={e => e.key === 'Enter' && handleAddTask()}
        />
        <button onClick={handleAddTask}>Add Task</button>

        <select value={priorityFilter} onChange={e => setPriorityFilter(e.target.value)}>
          <option value="all">All Priorities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>

        <input
          type="text"
          placeholder="Search..."
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
        />

        <button onClick={resetToSampleData}>Reset Sample Data</button>
      </div>

      {/* Board */}
      {searchQuery || priorityFilter !== 'all' ? (
        // Filtered view
        <div>
          <h2>🔍 Filtered Results ({filtered.length})</h2>
          {filtered.map(task => (
            <div key={task.id} style={{
              padding: '10px',
              margin: '5px',
              border: '1px solid #ccc',
              borderRadius: '4px',
              backgroundColor: '#f9f9f9'
            }}>
              <h3>{task.title}</h3>
              <p>{task.description}</p>
              <span style={{ fontWeight: 'bold' }}>{task.priority}</span>
              <button onClick={() => deleteTask(task.id)}>Delete</button>
            </div>
          ))}
        </div>
      ) : (
        // Normal board
        <div style={{ display: 'flex', gap: '20px' }}>
          {columnOrder.map(colId => {
            const column = columns[colId]
            return (
              <div key={colId} style={{
                flex: 1,
                border: '1px solid #ddd',
                borderRadius: '8px',
                padding: '15px',
                backgroundColor: '#f5f5f5'
              }}>
                <h2>{column.title}</h2>
                <div style={{ marginTop: '10px' }}>
                  {column.taskIds.map(taskId => {
                    const task = tasks[taskId]
                    return (
                      <div key={taskId} style={{
                        padding: '10px',
                        marginBottom: '10px',
                        backgroundColor: 'white',
                        border: '1px solid #e0e0e0',
                        borderRadius: '4px',
                        boxShadow: '0 1px 3px rgba(0,0,0,0.1)'
                      }}>
                        <h4 style={{ margin: '0 0 5px 0' }}>{task.title}</h4>
                        <p style={{ margin: '0 0 10px 0', fontSize: '0.9em', color: '#666' }}>
                          {task.description}
                        </p>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{
                            fontSize: '0.8em',
                            padding: '2px 8px',
                            borderRadius: '3px',
                            backgroundColor: task.priority === 'critical' ? '#ff6b6b' :
                                            task.priority === 'high' ? '#ffa94d' :
                                            task.priority === 'medium' ? '#4ecdc4' : '#95e1d3',
                            color: 'white'
                          }}>
                            {task.priority}
                          </span>
                          <button onClick={() => deleteTask(task.id)} style={{
                            padding: '2px 8px',
                            fontSize: '0.8em'
                          }}>
                            Delete
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

---

## All Available Methods

```jsx
const {
  // ─── STATE ───
  tasks,                        // Map of all tasks
  columns,                      // Map of all columns
  columnOrder,                  // Array of column IDs
  priorityFilter,               // Current priority filter
  searchQuery,                  // Current search query

  // ─── TASK ACTIONS ───
  addTask,                      // addTask(columnId, task)
  updateTask,                   // updateTask(taskId, updates)
  deleteTask,                   // deleteTask(taskId)
  moveTask,                     // moveTask(taskId, srcColId, dstColId, index)
  reorderTasks,                 // reorderTasks(columnId, taskIds)

  // ─── COLUMN ACTIONS ───
  addColumn,                    // addColumn(column)
  updateColumn,                 // updateColumn(columnId, updates)
  deleteColumn,                 // deleteColumn(columnId)
  reorderColumns,               // reorderColumns(newColumnOrder)

  // ─── FILTER ACTIONS ───
  setPriorityFilter,            // setPriorityFilter(priority)
  setSearchQuery,               // setSearchQuery(query)
  getFilteredTasks,             // getFilteredTasks() → Array

  // ─── UTILITIES ───
  resetToSampleData,            // resetToSampleData()
  clearAll,                     // clearAll()
  exportState,                  // exportState() → Object
  importState,                  // importState(state)
} = useKanbanStore()
```

---

## Sample Data (Pre-loaded)

| # | Task | Priority | Column | 
|---|------|----------|--------|
| 1 | Design new landing page | high | To Do |
| 2 | Fix authentication bug | critical | In Progress |
| 3 | Write documentation | medium | To Do |
| 4 | Code review PRs | high | In Progress |
| 5 | Optimize database queries | medium | Done |
| 6 | Update dependencies | low | To Do |

---

## localStorage

Automatically persists to `localStorage` under key `kanban-board-state`. Clear it manually:

```javascript
localStorage.removeItem('kanban-board-state')
location.reload()
```

---

## Next Steps

1. **Read the full guide**: `KANBAN_STORE_GUIDE.md`
2. **Check examples**: `KanbanExample.jsx`
3. **Explore architecture**: `README_KANBAN.md`
4. **Add drag & drop**: Integrate `react-beautiful-dnd`
5. **Style it**: Add CSS for your design system

---

## Tips

✅ **Always use `getFilteredTasks()`** instead of accessing `tasks` directly when filtering  
✅ **Combine filters**: Priority filter + search work together  
✅ **Export before big changes**: Use `exportState()` to backup  
✅ **Reset if corrupted**: `resetToSampleData()` gets you back to defaults  
✅ **Check console**: Errors are logged when localStorage fails  

---

**Happy Kanban-ing! 🎯**

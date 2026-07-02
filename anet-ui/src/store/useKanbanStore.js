import { create } from 'zustand'

const STORAGE_KEY = 'kanban-board-state'

/**
 * Sample tasks to seed on first load
 */
const SAMPLE_TASKS = [
  {
    id: 'task-1',
    title: 'Design new landing page',
    description: 'Create mockups and wireframes for the updated landing page',
    priority: 'high',
    dueDate: '2026-07-10',
    createdAt: '2026-07-02T10:00:00Z',
  },
  {
    id: 'task-2',
    title: 'Fix authentication bug',
    description: 'Session token expiration not handled correctly',
    priority: 'critical',
    dueDate: '2026-07-05',
    createdAt: '2026-07-02T09:30:00Z',
  },
  {
    id: 'task-3',
    title: 'Write documentation',
    description: 'Complete API reference documentation for v2.0',
    priority: 'medium',
    dueDate: '2026-07-15',
    createdAt: '2026-07-02T08:45:00Z',
  },
  {
    id: 'task-4',
    title: 'Code review PRs',
    description: 'Review pending pull requests from the team',
    priority: 'high',
    dueDate: '2026-07-03',
    createdAt: '2026-07-02T11:20:00Z',
  },
  {
    id: 'task-5',
    title: 'Optimize database queries',
    description: 'Reduce query execution time by 30% using proper indexing',
    priority: 'medium',
    dueDate: '2026-07-12',
    createdAt: '2026-07-02T07:15:00Z',
  },
  {
    id: 'task-6',
    title: 'Update dependencies',
    description: 'Update all npm packages to latest secure versions',
    priority: 'low',
    dueDate: '2026-07-20',
    createdAt: '2026-07-02T06:00:00Z',
  },
]

/**
 * Default column structure
 */
const DEFAULT_COLUMNS = {
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

/**
 * Load state from localStorage if available, otherwise use defaults
 */
const loadInitialState = () => {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      const parsed = JSON.parse(saved)
      return {
        tasks: parsed.tasks || {},
        columns: parsed.columns || DEFAULT_COLUMNS,
        columnOrder: parsed.columnOrder || Object.keys(DEFAULT_COLUMNS),
      }
    }
  } catch (error) {
    console.warn('Failed to load Kanban state from localStorage:', error)
  }

  // First load: create tasks and columns from sample data
  const tasksMap = {}
  SAMPLE_TASKS.forEach((task) => {
    tasksMap[task.id] = task
  })

  return {
    tasks: tasksMap,
    columns: DEFAULT_COLUMNS,
    columnOrder: Object.keys(DEFAULT_COLUMNS),
  }
}

/**
 * Persist state to localStorage
 */
const persistState = (state) => {
  try {
    const toSave = {
      tasks: state.tasks,
      columns: state.columns,
      columnOrder: state.columnOrder,
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave))
  } catch (error) {
    console.error('Failed to persist Kanban state to localStorage:', error)
  }
}

export const useKanbanStore = create((set, get) => {
  const initialState = loadInitialState()

  return {
    // ═══════════════════════════════════════════════════════════
    // STATE
    // ═══════════════════════════════════════════════════════════

    // Board state
    tasks: initialState.tasks,
    columns: initialState.columns,
    columnOrder: initialState.columnOrder,

    // Filter state
    priorityFilter: 'all', // 'all', 'critical', 'high', 'medium', 'low'
    searchQuery: '',

    // ═══════════════════════════════════════════════════════════
    // TASK MANAGEMENT
    // ═══════════════════════════════════════════════════════════

    /**
     * Add a new task to a column
     */
    addTask: (columnId, task) =>
      set((state) => {
        const newTask = {
          ...task,
          id: task.id || `task-${Date.now()}`,
          createdAt: task.createdAt || new Date().toISOString(),
        }

        const newState = {
          tasks: { ...state.tasks, [newTask.id]: newTask },
          columns: {
            ...state.columns,
            [columnId]: {
              ...state.columns[columnId],
              taskIds: [...state.columns[columnId].taskIds, newTask.id],
            },
          },
        }

        persistState(newState)
        return newState
      }),

    /**
     * Update an existing task
     */
    updateTask: (taskId, updates) =>
      set((state) => {
        const newState = {
          tasks: {
            ...state.tasks,
            [taskId]: { ...state.tasks[taskId], ...updates },
          },
        }

        persistState(newState)
        return newState
      }),

    /**
     * Delete a task from a column
     */
    deleteTask: (taskId) =>
      set((state) => {
        const { [taskId]: _, ...remainingTasks } = state.tasks

        // Find which column contains this task
        const updatedColumns = { ...state.columns }
        Object.keys(updatedColumns).forEach((colId) => {
          updatedColumns[colId] = {
            ...updatedColumns[colId],
            taskIds: updatedColumns[colId].taskIds.filter((id) => id !== taskId),
          }
        })

        const newState = {
          tasks: remainingTasks,
          columns: updatedColumns,
        }

        persistState(newState)
        return newState
      }),

    /**
     * Move a task to a different column
     */
    moveTask: (taskId, sourceColumnId, destinationColumnId, destinationIndex) =>
      set((state) => {
        const sourceColumn = state.columns[sourceColumnId]
        const destColumn = state.columns[destinationColumnId]

        const sourceTaskIds = sourceColumn.taskIds.filter((id) => id !== taskId)
        const destTaskIds = [
          ...destColumn.taskIds.slice(0, destinationIndex),
          taskId,
          ...destColumn.taskIds.slice(destinationIndex),
        ]

        const newState = {
          columns: {
            ...state.columns,
            [sourceColumnId]: { ...sourceColumn, taskIds: sourceTaskIds },
            [destinationColumnId]: { ...destColumn, taskIds: destTaskIds },
          },
        }

        persistState(newState)
        return newState
      }),

    /**
     * Reorder tasks within the same column
     */
    reorderTasks: (columnId, taskIds) =>
      set((state) => {
        const newState = {
          columns: {
            ...state.columns,
            [columnId]: { ...state.columns[columnId], taskIds },
          },
        }

        persistState(newState)
        return newState
      }),

    // ═══════════════════════════════════════════════════════════
    // COLUMN MANAGEMENT
    // ═══════════════════════════════════════════════════════════

    /**
     * Add a new column
     */
    addColumn: (column) =>
      set((state) => {
        const newColumn = {
          ...column,
          id: column.id || `col-${Date.now()}`,
          taskIds: column.taskIds || [],
        }

        const newState = {
          columns: { ...state.columns, [newColumn.id]: newColumn },
          columnOrder: [...state.columnOrder, newColumn.id],
        }

        persistState(newState)
        return newState
      }),

    /**
     * Update a column (e.g., rename it)
     */
    updateColumn: (columnId, updates) =>
      set((state) => {
        const newState = {
          columns: {
            ...state.columns,
            [columnId]: { ...state.columns[columnId], ...updates },
          },
        }

        persistState(newState)
        return newState
      }),

    /**
     * Delete a column (tasks are kept; you decide how to handle them)
     */
    deleteColumn: (columnId) =>
      set((state) => {
        const { [columnId]: _, ...remainingColumns } = state.columns

        const newState = {
          columns: remainingColumns,
          columnOrder: state.columnOrder.filter((id) => id !== columnId),
        }

        persistState(newState)
        return newState
      }),

    /**
     * Reorder columns
     */
    reorderColumns: (newColumnOrder) =>
      set((state) => {
        const newState = { columnOrder: newColumnOrder }

        persistState(newState)
        return newState
      }),

    // ═══════════════════════════════════════════════════════════
    // FILTER & SEARCH
    // ═══════════════════════════════════════════════════════════

    /**
     * Set priority filter
     */
    setPriorityFilter: (priority) => set({ priorityFilter: priority }),

    /**
     * Set search query
     */
    setSearchQuery: (query) => set({ searchQuery: query }),

    /**
     * Get filtered and searched tasks
     */
    getFilteredTasks: () => {
      const state = get()
      const { tasks, priorityFilter, searchQuery } = state

      return Object.values(tasks).filter((task) => {
        // Priority filter
        if (priorityFilter !== 'all' && task.priority !== priorityFilter) {
          return false
        }

        // Search filter
        if (searchQuery.trim()) {
          const query = searchQuery.toLowerCase()
          const matchesTitle = task.title.toLowerCase().includes(query)
          const matchesDescription = task.description?.toLowerCase().includes(query)
          return matchesTitle || matchesDescription
        }

        return true
      })
    },

    // ═══════════════════════════════════════════════════════════
    // UTILITY
    // ═══════════════════════════════════════════════════════════

    /**
     * Reset board to sample data
     */
    resetToSampleData: () => {
      const tasksMap = {}
      SAMPLE_TASKS.forEach((task) => {
        tasksMap[task.id] = task
      })

      const newState = {
        tasks: tasksMap,
        columns: DEFAULT_COLUMNS,
        columnOrder: Object.keys(DEFAULT_COLUMNS),
      }

      persistState(newState)
      set(newState)
    },

    /**
     * Clear all data (for testing)
     */
    clearAll: () => {
      const newState = {
        tasks: {},
        columns: {},
        columnOrder: [],
      }

      persistState(newState)
      set(newState)
    },

    /**
     * Export current state as JSON (for backup/debugging)
     */
    exportState: () => {
      const state = get()
      return {
        tasks: state.tasks,
        columns: state.columns,
        columnOrder: state.columnOrder,
        exportedAt: new Date().toISOString(),
      }
    },

    /**
     * Import state from JSON
     */
    importState: (importedState) => {
      const newState = {
        tasks: importedState.tasks || {},
        columns: importedState.columns || {},
        columnOrder: importedState.columnOrder || [],
      }

      persistState(newState)
      set(newState)
    },
  }
})

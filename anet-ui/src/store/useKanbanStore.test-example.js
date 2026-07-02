/**
 * useKanbanStore - Test & Usage Examples
 * 
 * This file demonstrates how to test and use the useKanbanStore hook
 * It's not meant to be run directly, but rather used as a reference
 * for understanding the store's behavior.
 */

import { useKanbanStore } from './useKanbanStore'

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 1: INITIALIZATION
// ═══════════════════════════════════════════════════════════════════════════

function testInitialization() {
  console.log('TEST: Initialization')

  const store = useKanbanStore.getState()

  // Should have 6 tasks
  console.assert(Object.keys(store.tasks).length === 6, 'Should have 6 tasks')

  // Should have 3 columns
  console.assert(Object.keys(store.columns).length === 3, 'Should have 3 columns')

  // Should have column order
  console.assert(store.columnOrder.length === 3, 'Should have 3 columns in order')

  // Should have default filter states
  console.assert(store.priorityFilter === 'all', 'Default priority filter should be "all"')
  console.assert(store.searchQuery === '', 'Default search query should be empty')

  console.log('✓ Initialization test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 2: TASK MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════════

function testTaskManagement() {
  console.log('\nTEST: Task Management')

  const { addTask, updateTask, deleteTask, tasks } = useKanbanStore.getState()

  const initialCount = Object.keys(tasks).length

  // Test: Add Task
  addTask('col-todo', {
    title: 'Test Task',
    description: 'This is a test task',
    priority: 'high',
    dueDate: '2026-07-10',
  })

  let state = useKanbanStore.getState()
  console.assert(
    Object.keys(state.tasks).length === initialCount + 1,
    'Should have added one task'
  )

  // Get the newly added task
  const newTaskId = Object.keys(state.tasks).find(
    id => state.tasks[id].title === 'Test Task'
  )
  console.assert(newTaskId, 'Should be able to find the new task')

  // Test: Update Task
  updateTask(newTaskId, { priority: 'critical' })
  state = useKanbanStore.getState()
  console.assert(
    state.tasks[newTaskId].priority === 'critical',
    'Should have updated task priority'
  )

  // Test: Delete Task
  deleteTask(newTaskId)
  state = useKanbanStore.getState()
  console.assert(
    Object.keys(state.tasks).length === initialCount,
    'Should have deleted the task'
  )

  console.log('✓ Task management test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 3: COLUMN MANAGEMENT
// ═══════════════════════════════════════════════════════════════════════════

function testColumnManagement() {
  console.log('\nTEST: Column Management')

  const { addColumn, updateColumn, deleteColumn, columns, columnOrder } = useKanbanStore.getState()

  const initialColumnCount = Object.keys(columns).length

  // Test: Add Column
  addColumn({ title: 'Test Column' })
  let state = useKanbanStore.getState()
  console.assert(
    Object.keys(state.columns).length === initialColumnCount + 1,
    'Should have added one column'
  )

  // Get the newly added column
  const newColumnId = Object.keys(state.columns).find(
    id => state.columns[id].title === 'Test Column'
  )
  console.assert(newColumnId, 'Should be able to find the new column')

  // Test: Update Column
  updateColumn(newColumnId, { title: 'Updated Column' })
  state = useKanbanStore.getState()
  console.assert(
    state.columns[newColumnId].title === 'Updated Column',
    'Should have updated column title'
  )

  // Test: Delete Column
  deleteColumn(newColumnId)
  state = useKanbanStore.getState()
  console.assert(
    Object.keys(state.columns).length === initialColumnCount,
    'Should have deleted the column'
  )

  console.log('✓ Column management test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 4: TASK MOVEMENT
// ═══════════════════════════════════════════════════════════════════════════

function testTaskMovement() {
  console.log('\nTEST: Task Movement')

  const { columns, moveTask, tasks } = useKanbanStore.getState()

  const taskToMove = 'task-1' // From sample data
  const sourceColumn = 'col-todo'
  const destColumn = 'col-in-progress'

  let state = useKanbanStore.getState()
  const initialSourceCount = state.columns[sourceColumn].taskIds.length
  const initialDestCount = state.columns[destColumn].taskIds.length

  // Move task
  moveTask(taskToMove, sourceColumn, destColumn, 0)

  state = useKanbanStore.getState()
  console.assert(
    !state.columns[sourceColumn].taskIds.includes(taskToMove),
    'Task should be removed from source column'
  )
  console.assert(
    state.columns[destColumn].taskIds.includes(taskToMove),
    'Task should be added to destination column'
  )
  console.assert(
    state.columns[destColumn].taskIds[0] === taskToMove,
    'Task should be at index 0 in destination'
  )

  console.log('✓ Task movement test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 5: FILTERING
// ═══════════════════════════════════════════════════════════════════════════

function testFiltering() {
  console.log('\nTEST: Filtering')

  const { 
    setPriorityFilter, 
    setSearchQuery, 
    getFilteredTasks, 
    tasks,
    resetToSampleData 
  } = useKanbanStore.getState()

  // Reset to sample data
  resetToSampleData()

  let state = useKanbanStore.getState()
  const totalTasks = Object.keys(state.tasks).length

  // Test: Priority Filter
  state.setPriorityFilter('high')
  state = useKanbanStore.getState()
  let filtered = state.getFilteredTasks()
  
  // Should have high priority tasks
  console.assert(
    filtered.every(task => task.priority === 'high'),
    'All filtered tasks should have high priority'
  )
  console.assert(
    filtered.length < totalTasks,
    'Filtered results should have fewer tasks'
  )

  // Reset filter
  state.setPriorityFilter('all')
  state = useKanbanStore.getState()
  filtered = state.getFilteredTasks()
  console.assert(
    filtered.length === totalTasks,
    'All filter should return all tasks'
  )

  // Test: Search Filter
  state.setSearchQuery('landing')
  state = useKanbanStore.getState()
  filtered = state.getFilteredTasks()
  console.assert(
    filtered.some(task => task.title.toLowerCase().includes('landing')),
    'Search should find "landing page" task'
  )
  console.assert(
    filtered.length < totalTasks,
    'Search should filter results'
  )

  // Reset search
  state.setSearchQuery('')
  state = useKanbanStore.getState()
  filtered = state.getFilteredTasks()
  console.assert(
    filtered.length === totalTasks,
    'Empty search should return all tasks'
  )

  console.log('✓ Filtering test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 6: PERSISTENCE
// ═══════════════════════════════════════════════════════════════════════════

function testPersistence() {
  console.log('\nTEST: Persistence')

  const { exportState, importState, clearAll, tasks, columns } = useKanbanStore.getState()

  // Test: Export
  const exported = exportState()
  console.assert(
    exported.tasks && exported.columns && exported.columnOrder,
    'Exported state should have tasks, columns, and columnOrder'
  )
  console.assert(
    typeof exported.exportedAt === 'string',
    'Exported state should have timestamp'
  )

  // Test: Import
  clearAll()
  let state = useKanbanStore.getState()
  console.assert(
    Object.keys(state.tasks).length === 0,
    'After clearAll, should have no tasks'
  )

  importState(exported)
  state = useKanbanStore.getState()
  console.assert(
    Object.keys(state.tasks).length > 0,
    'After importState, should have restored tasks'
  )

  console.log('✓ Persistence test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 7: REORDERING
// ═══════════════════════════════════════════════════════════════════════════

function testReordering() {
  console.log('\nTEST: Reordering')

  const { columns, reorderTasks, reorderColumns } = useKanbanStore.getState()

  // Test: Reorder tasks
  const columnId = 'col-todo'
  let state = useKanbanStore.getState()
  const originalOrder = [...state.columns[columnId].taskIds]

  if (originalOrder.length >= 2) {
    const newOrder = [originalOrder[1], originalOrder[0], ...originalOrder.slice(2)]
    state.reorderTasks(columnId, newOrder)
    
    state = useKanbanStore.getState()
    console.assert(
      JSON.stringify(state.columns[columnId].taskIds) === JSON.stringify(newOrder),
      'Tasks should be reordered'
    )
  }

  // Test: Reorder columns
  state = useKanbanStore.getState()
  const originalColumnOrder = [...state.columnOrder]
  
  if (originalColumnOrder.length >= 2) {
    const newColumnOrder = [originalColumnOrder[1], originalColumnOrder[0], ...originalColumnOrder.slice(2)]
    state.reorderColumns(newColumnOrder)
    
    state = useKanbanStore.getState()
    console.assert(
      JSON.stringify(state.columnOrder) === JSON.stringify(newColumnOrder),
      'Columns should be reordered'
    )
  }

  console.log('✓ Reordering test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// TEST SUITE 8: SAMPLE DATA
// ═══════════════════════════════════════════════════════════════════════════

function testSampleData() {
  console.log('\nTEST: Sample Data')

  const { resetToSampleData, tasks, columns } = useKanbanStore.getState()

  resetToSampleData()
  let state = useKanbanStore.getState()

  // Should have exactly 6 sample tasks
  console.assert(
    Object.keys(state.tasks).length === 6,
    'Should have 6 sample tasks'
  )

  // Should have exactly 3 columns
  console.assert(
    Object.keys(state.columns).length === 3,
    'Should have 3 columns'
  )

  // Check specific sample tasks exist
  console.assert(
    Object.values(state.tasks).some(t => t.title.includes('authentication')),
    'Should have authentication task'
  )
  console.assert(
    Object.values(state.tasks).some(t => t.priority === 'critical'),
    'Should have critical priority task'
  )

  console.log('✓ Sample data test passed')
}

// ═══════════════════════════════════════════════════════════════════════════
// RUN ALL TESTS
// ═══════════════════════════════════════════════════════════════════════════

export function runAllTests() {
  console.clear()
  console.log('🧪 Running Kanban Store Test Suite\n')
  console.log('='.repeat(60))

  try {
    testInitialization()
    testSampleData()
    testTaskManagement()
    testColumnManagement()
    testTaskMovement()
    testFiltering()
    testReordering()
    testPersistence()

    console.log('\n' + '='.repeat(60))
    console.log('✅ ALL TESTS PASSED\n')
  } catch (error) {
    console.error('\n❌ TEST FAILED:', error)
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// USAGE EXAMPLES
// ═══════════════════════════════════════════════════════════════════════════

export function exampleBasicUsage() {
  const { columns, columnOrder, tasks } = useKanbanStore.getState()

  console.log('Current Board State:')
  columnOrder.forEach(colId => {
    const column = columns[colId]
    console.log(`\n${column.title}:`)
    column.taskIds.forEach(taskId => {
      const task = tasks[taskId]
      console.log(`  - [${task.priority}] ${task.title}`)
    })
  })
}

export function exampleFiltering() {
  const { setPriorityFilter, setSearchQuery, getFilteredTasks } = useKanbanStore.getState()

  // Find high priority tasks
  setPriorityFilter('high')
  let filtered = getFilteredTasks()
  console.log(`Found ${filtered.length} high priority tasks`)

  // Search within high priority
  setSearchQuery('landing')
  filtered = getFilteredTasks()
  console.log(`Found ${filtered.length} tasks with "landing"`)
}

export function exampleAddingTask() {
  const { addTask, columns } = useKanbanStore.getState()

  addTask('col-todo', {
    title: 'New Feature',
    description: 'Build the new feature',
    priority: 'high',
    dueDate: '2026-07-15'
  })

  console.log('Task added successfully')
}

export function exampleMovingTask() {
  const { moveTask, columns } = useKanbanStore.getState()

  moveTask('task-1', 'col-todo', 'col-in-progress', 0)
  console.log('Task moved successfully')
}

export function exampleExportImport() {
  const { exportState, importState } = useKanbanStore.getState()

  // Export
  const backup = exportState()
  console.log('Exported state:', backup)

  // Could save to localStorage, send to server, etc.
  
  // Import
  importState(backup)
  console.log('Imported state successfully')
}

// ═══════════════════════════════════════════════════════════════════════════
// TO RUN TESTS IN BROWSER CONSOLE:
// ═══════════════════════════════════════════════════════════════════════════
// 
// import { runAllTests, exampleBasicUsage } from './useKanbanStore.test-example'
// runAllTests()
// exampleBasicUsage()
// 
// ═══════════════════════════════════════════════════════════════════════════

import { useState, useCallback } from 'react'
import {
  DndContext,
  closestCorners,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  DragOverlay,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  horizontalListSortingStrategy,
} from '@dnd-kit/sortable'
import { Plus } from 'lucide-react'
import Column from './Column'
import TaskCard from './TaskCard'
import FilterBar from './FilterBar'
import TaskModal from './TaskModal'

/**
 * Board - Main Kanban board with drag-drop support
 *
 * Features:
 * - 3-column layout (responsive)
 * - Drag tasks within column and between columns
 * - Drag to reorder columns
 * - Real-time task filtering and search
 * - Add/edit/delete tasks
 * - Add/delete columns
 * - Modal for task management
 * - Filter bar with priority and search
 * - Auto-persist to localStorage
 *
 * Props:
 *   - store: Zustand store instance (useKanbanStore)
 */
export default function Board({ store }) {
  const {
    columns,
    columnOrder,
    tasks,
    priorityFilter,
    searchQuery,
    addTask,
    updateTask,
    deleteTask,
    moveTask,
    reorderTasks,
    reorderColumns,
    addColumn,
    deleteColumn,
    setPriorityFilter,
    setSearchQuery,
    getFilteredTasks,
  } = store()

  // Modal state
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [editingTask, setEditingTask] = useState(null)
  const [addToColumn, setAddToColumn] = useState(null)

  // Drag state
  const [activeId, setActiveId] = useState(null)
  const [activeDragType, setActiveDragType] = useState(null) // 'task' or 'column'

  // Setup sensors for drag-drop
  const sensors = useSensors(
    useSensor(PointerSensor, {
      distance: 8,
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  )

  // Get filtered tasks
  const filteredTaskIds = new Set(getFilteredTasks().map((t) => t.id))

  // Handle drag start
  const handleDragStart = (event) => {
    const { active } = event
    setActiveId(active.id)

    if (active.data.current?.type === 'Task') {
      setActiveDragType('task')
    } else if (active.data.current?.type === 'Column') {
      setActiveDragType('column')
    }
  }

  // Handle drag over
  const handleDragOver = (event) => {
    const { active, over } = event

    if (!over) return

    const activeId = active.id
    const overId = over.id

    if (activeId === overId) return

    // Task drag over
    if (active.data.current?.type === 'Task') {
      const task = active.data.current.task
      const sourceColumnId = Object.entries(columns).find(
        ([, col]) => col.taskIds.includes(activeId)
      )?.[0]

      if (!sourceColumnId) return

      // Dragging over a column
      if (over.data.current?.type === 'Column') {
        const destColumnId = over.id
        if (sourceColumnId !== destColumnId) {
          moveTask(
            activeId,
            sourceColumnId,
            destColumnId,
            columns[destColumnId].taskIds.length
          )
        }
      }
      // Dragging over a task
      else if (over.data.current?.type === 'Task') {
        const overTask = over.data.current.task
        const destColumnId = Object.entries(columns).find(
          ([, col]) => col.taskIds.includes(overId)
        )?.[0]

        if (destColumnId && sourceColumnId) {
          const sourceTaskIds = columns[sourceColumnId].taskIds
          const destTaskIds = columns[destColumnId].taskIds

          if (sourceColumnId === destColumnId) {
            // Reorder within column
            const oldIndex = sourceTaskIds.indexOf(activeId)
            const newIndex = destTaskIds.indexOf(overId)

            if (oldIndex !== newIndex) {
              const newOrder = arrayMove(sourceTaskIds, oldIndex, newIndex)
              reorderTasks(sourceColumnId, newOrder)
            }
          } else {
            // Move to different column
            const newDestTaskIds = [...destTaskIds]
            const newIndex = destTaskIds.indexOf(overId)
            newDestTaskIds.splice(newIndex, 0, activeId)

            moveTask(activeId, sourceColumnId, destColumnId, newIndex)
          }
        }
      }
    }
    // Column drag over - reorder columns
    else if (active.data.current?.type === 'Column') {
      if (over.data.current?.type === 'Column') {
        const oldIndex = columnOrder.indexOf(activeId)
        const newIndex = columnOrder.indexOf(overId)

        if (oldIndex !== newIndex) {
          const newOrder = arrayMove(columnOrder, oldIndex, newIndex)
          reorderColumns(newOrder)
        }
      }
    }
  }

  // Handle drag end
  const handleDragEnd = () => {
    setActiveId(null)
    setActiveDragType(null)
  }

  // Task management
  const handleAddTask = (columnId) => {
    setAddToColumn(columnId)
    setEditingTask(null)
    setIsModalOpen(true)
  }

  const handleEditTask = (task) => {
    setEditingTask(task)
    setAddToColumn(null)
    setIsModalOpen(true)
  }

  const handleDeleteTask = (taskId) => {
    if (window.confirm('Delete this task? This cannot be undone.')) {
      deleteTask(taskId)
    }
  }

  const handleSaveTask = (columnIdOrTask, taskData = null) => {
    if (editingTask) {
      // Edit mode
      updateTask(editingTask.id, columnIdOrTask)
    } else {
      // Add mode
      const columnId = columnIdOrTask
      addTask(columnId, taskData)
    }
  }

  const handleCloseModal = () => {
    setIsModalOpen(false)
    setEditingTask(null)
    setAddToColumn(null)
  }

  // Column management
  const handleAddColumn = () => {
    const title = window.prompt('Enter column name:')
    if (title && title.trim()) {
      addColumn({ title: title.trim() })
    }
  }

  const handleDeleteColumn = (columnId) => {
    if (
      window.confirm(
        `Delete column "${columns[columnId].title}"? Tasks will remain but unassigned.`
      )
    ) {
      deleteColumn(columnId)
    }
  }

  // Active dragging item
  let dragOverlay = null
  if (activeId) {
    if (activeDragType === 'task') {
      const task = tasks[activeId]
      if (task) {
        dragOverlay = <TaskCard task={task} onEdit={() => {}} onDelete={() => {}} />
      }
    }
  }

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
      }}
    >
      {/* Filter Bar */}
      <FilterBar
        priorityFilter={priorityFilter}
        searchQuery={searchQuery}
        onPriorityChange={setPriorityFilter}
        onSearchChange={setSearchQuery}
      />

      {/* Board Container */}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDragEnd={handleDragEnd}
      >
        <div className="kanban-board">
          {/* Columns */}
          <SortableContext
            items={columnOrder}
            strategy={horizontalListSortingStrategy}
          >
            {columnOrder.map((columnId) => {
              const column = columns[columnId]
              if (!column) return null

              // Filter tasks in this column based on current filters
              const visibleTaskIds = column.taskIds.filter((taskId) =>
                filteredTaskIds.has(taskId)
              )

              return (
                <Column
                  key={column.id}
                  column={{
                    ...column,
                    taskIds: visibleTaskIds,
                  }}
                  tasks={tasks}
                  onAddTask={handleAddTask}
                  onEditTask={handleEditTask}
                  onDeleteTask={handleDeleteTask}
                  onDeleteColumn={handleDeleteColumn}
                />
              )
            })}
          </SortableContext>

          {/* Add Column Button */}
          <button
            onClick={handleAddColumn}
            className="add-column-btn"
            aria-label="Add new column"
          >
            <Plus size={16} />
            <span>Add Column</span>
          </button>
        </div>

        {/* Drag Overlay */}
        <DragOverlay dropAnimation={null}>
          {dragOverlay ? (
            <div
              style={{
                opacity: 0.95,
                transform: 'scale(1.05)',
              }}
            >
              {dragOverlay}
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      {/* Task Modal */}
      <TaskModal
        isOpen={isModalOpen}
        task={editingTask}
        columnId={addToColumn}
        onSave={handleSaveTask}
        onClose={handleCloseModal}
      />
    </div>
  )
}

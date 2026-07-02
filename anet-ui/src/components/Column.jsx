import { useDroppable } from '@dnd-kit/core'
import {
  SortableContext,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { Plus, GripVertical, Trash2 } from 'lucide-react'
import TaskCard from './TaskCard'

/**
 * Column - Droppable column with task cards
 *
 * Features:
 * - Header with column title and task counter
 * - Droppable zone for drag-drop
 * - Task list with drag-drop reordering
 * - Add task button
 * - Delete column button
 * - Empty state message
 * - Visual feedback for drag-over state
 * - Task editing and deletion
 *
 * Props:
 *   - column: object - { id, title, taskIds }
 *   - tasks: object - { taskId: taskData }
 *   - onAddTask: (columnId) => void - add task callback
 *   - onEditTask: (task) => void - edit task callback
 *   - onDeleteTask: (taskId) => void - delete task callback
 *   - onDeleteColumn: (columnId) => void - delete column callback (optional)
 */
export default function Column({
  column,
  tasks,
  onAddTask,
  onEditTask,
  onDeleteTask,
  onDeleteColumn,
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: column.id,
    data: { type: 'Column', column },
  })

  const taskIds = column.taskIds || []
  const columnTasks = taskIds
    .map((taskId) => tasks[taskId])
    .filter(Boolean)

  const taskCount = taskIds.length

  return (
    <div
      ref={setNodeRef}
      className={`kanban-column ${isOver ? 'drag-over' : ''}`}
    >
      {/* Column Header */}
      <div className="kanban-column-header">
        {/* Title and Count */}
        <div className="kanban-column-title">
          <GripVertical
            size={16}
            style={{
              color: 'var(--text-muted)',
              flexShrink: 0,
              cursor: 'grab',
            }}
          />
          <h3>{column.title}</h3>
        </div>

        {/* Task Counter Badge */}
        <div className="task-counter">{taskCount}</div>

        {/* Delete Column Button */}
        {onDeleteColumn && (
          <button
            className="icon-btn"
            onClick={() => onDeleteColumn(column.id)}
            title="Delete column"
            aria-label={`Delete column ${column.title}`}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>

      {/* Tasks Container */}
      <div className="kanban-tasks">
        <SortableContext
          items={taskIds}
          strategy={verticalListSortingStrategy}
        >
          {columnTasks.length === 0 ? (
            /* Empty State */
            <div className="kanban-empty-state">
              <div className="kanban-empty-state-text">No tasks yet</div>
              <div className="kanban-empty-state-hint">Add one to get started</div>
            </div>
          ) : (
            /* Task List */
            columnTasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onEdit={onEditTask}
                onDelete={onDeleteTask}
              />
            ))
          )}
        </SortableContext>
      </div>

      {/* Add Task Button */}
      <div className="kanban-add-task-btn">
        <button
          className="btn btn-dashed"
          onClick={() => onAddTask(column.id)}
          aria-label={`Add task to ${column.title}`}
        >
          <Plus size={14} />
          <span>Add Task</span>
        </button>
      </div>
    </div>
  )
}

/**
 * KanbanBoard - Main Kanban board component
 *
 * Drop-in replacement for KanbanExample that uses all the new components:
 * - Board.jsx (main board with drag-drop)
 * - Column.jsx (droppable columns)
 * - TaskCard.jsx (individual task cards)
 * - TaskModal.jsx (add/edit form)
 * - FilterBar.jsx (priority + search)
 *
 * Usage:
 * ```jsx
 * import KanbanBoard from './components/KanbanBoard'
 * import { useKanbanStore } from './store/useKanbanStore'
 *
 * export default function App() {
 *   const store = useKanbanStore
 *   return <KanbanBoard store={store} />
 * }
 * ```
 */

import Board from './Board'

export default function KanbanBoard({ store }) {
  return (
    <div style={{ width: '100%', height: '100%' }}>
      <Board store={store} />
    </div>
  )
}
